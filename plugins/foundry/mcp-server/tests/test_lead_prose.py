"""Pins the LEAD-facing half of the protocol: the two command files, the
rationale reference, the temper skill, the setup script and both READMEs.

Why a second prose module rather than more assertions in
``test_protocol_prose.py``: that file pins what the STREAM AGENTS and the
teammate are told, and it is already four thousand lines. This one pins what
the LEAD is told -- the lane it may fix inside, the tools it can reach, how a
defect family stops recurring, what the report must carry, and how a run that
targets foundry itself is launched. The two populations drift independently and
fail for different reasons, so they get different modules.

Every assertion below answers a concrete failure. The ones that are not obvious
carry the requirement id and that failure in a comment beside them.
"""

from __future__ import annotations

import ast
import json
import re
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
FOUNDRY_ROOT = REPO_ROOT / "plugins" / "foundry"

COMMANDS = FOUNDRY_ROOT / "commands"
REFERENCES = FOUNDRY_ROOT / "references"
SKILLS = FOUNDRY_ROOT / "skills"
SCRIPTS = FOUNDRY_ROOT / "scripts"
MCP_SRC = FOUNDRY_ROOT / "mcp-server" / "src" / "foundry_mcp"
AGENTS = FOUNDRY_ROOT / "agents"

START_MD = COMMANDS / "start.md"
HELP_MD = COMMANDS / "help.md"
LEAD_DISCIPLINE = REFERENCES / "lead-discipline.md"
TEMPER_SKILL = SKILLS / "temper" / "SKILL.md"
SETUP_SH = SCRIPTS / "setup-foundry.sh"
PLUGIN_README = FOUNDRY_ROOT / "README.md"
ROOT_README = REPO_ROOT / "README.md"

#: The source copy of the shared filing rules. Every defect-filing surface's
#: block is copied from here, so the sweep below compares against it rather
#: than against a second literal in this module -- a literal is a copy free to
#: drift from both, which is the defect the sweep exists to catch.
ASSAYER = AGENTS / "assayer.md"

SERVER_PY = MCP_SRC / "server.py"
PLUGIN_MANIFEST = FOUNDRY_ROOT / ".claude-plugin" / "plugin.json"
PYPROJECT = FOUNDRY_ROOT / "mcp-server" / "pyproject.toml"


def _read(path: Path) -> str:
    """Read a pinned file, failing loudly if it moved or was deleted."""
    assert path.is_file(), (
        f"{path.relative_to(REPO_ROOT)} does not exist. If the file was "
        f"renamed, update this module's path constants -- do not delete the "
        f"assertions, the prose they pin is still required."
    )
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _flat(path: Path) -> str:
    """``_read`` with every run of whitespace collapsed to one space.

    Markdown wraps a sentence wherever the author's column limit fell, and
    where that break lands is not a property worth pinning. Phrase-level pins
    read the flattened text so a reflow that changed no words cannot fail them.
    """
    return " ".join(_read(path).split())


# ---------------------------------------------------------------------------
# The MCP tools table (AC-005 / FR-026)
# ---------------------------------------------------------------------------
#
# start.md's tools table listed a SUBSET of what the server registers -- twenty
# of thirty-four -- so `Foundry-Observation`, `Foundry-Directive`, `Foundry-Clear`
# and the rest were discoverable only by watching a run use them, or by reading
# server.py. An operator cannot call a tool whose existence the protocol never
# mentions.
#
# The expected set is DERIVED from the registration source and never typed out
# here. A hand-maintained list in this file would be a second copy free to drift
# from the first, which is precisely the failure the test exists to catch: it
# would go green the day someone updated the test instead of the table.


def _registered_tool_names() -> frozenset[str]:
    """Every ``name=`` on a ``Tool(...)`` construction in server.py.

    Parsed with ``ast`` rather than grepped, and rather than imported.

    Not a regex: a pattern anchored on ``Tool(\\s*name="..."`` matches today's
    formatting and silently returns FEWER names the day a registration is
    reflowed onto one line. A derivation that fails open is worse than none,
    because the assertion built on it still passes.

    Not an import either: ``list_tools`` is an async handler registered through
    the MCP SDK's decorator, so reaching it means reaching into SDK internals
    that are not this repo's contract. The source is the contract.
    """
    tree = ast.parse(_read(SERVER_PY))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", None) != "Tool":
            continue
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                names.add(keyword.value.value)
    return frozenset(names)


_TOOLS_HEADING = "## MCP TOOLS REFERENCE"


def _tools_table() -> str:
    """start.md's MCP tools section, heading to the next top-level heading."""
    text = _read(START_MD)
    start = text.find(_TOOLS_HEADING)
    assert start != -1, (
        f"{_rel(START_MD)} has no {_TOOLS_HEADING!r} section. That table is the "
        f"only place the lead learns which tools exist; without it there is "
        f"nothing for this module to compare against."
    )
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_the_tool_name_derivation_is_not_vacuous() -> None:
    """A derivation that silently stops matching makes every check below pass.

    If ``_registered_tool_names`` ever returned the empty set -- server.py
    restructured, ``Tool`` aliased at import, the walk broken -- the
    completeness assertion would iterate nothing and report success on a table
    that listed no tools at all. Assert the floor before trusting anything
    built on top of it.
    """
    names = _registered_tool_names()
    assert len(names) >= 30, (
        f"only {len(names)} tool name(s) derived from {_rel(SERVER_PY)}: "
        f"{sorted(names)}. The server registers well over thirty; a derivation "
        f"this small means the AST walk stopped matching the registration "
        f"shape, not that tools were removed."
    )
    # Sentinels from opposite ends of the registration list, so a walk that
    # captured only the first block or only the last would still be caught.
    assert "Validate-Report" in names, sorted(names)
    assert "Forge-Spec-Status" in names, sorted(names)


def test_start_md_tools_table_lists_every_registered_tool() -> None:
    """AC-005 / FR-026: every registered tool appears in start.md's table.

    Compared as SETS so the failure names the specific tools that are missing.
    A substring check over the whole file would pass on a tool mentioned in a
    phase step but absent from the reference table, which is the one place an
    operator looks when they want to know what exists.
    """
    table = _tools_table()
    missing = sorted(name for name in _registered_tool_names() if name not in table)
    assert not missing, (
        f"{_rel(START_MD)}'s {_TOOLS_HEADING} table omits {missing}. Every tool "
        f"{_rel(SERVER_PY)} registers must have a row, or the lead can only "
        f"discover it by watching a run use it. Add a row per tool; do not "
        f"relax this assertion."
    )


# ---------------------------------------------------------------------------
# Version agreement between the badges and the manifests (AC-040 / FR-027)
# ---------------------------------------------------------------------------


def _plugin_version() -> str:
    return json.loads(_read(PLUGIN_MANIFEST))["version"]


def _server_version() -> str:
    return tomllib.loads(_read(PYPROJECT))["project"]["version"]


@pytest.mark.parametrize("readme", (PLUGIN_README, ROOT_README), ids=_rel)
def test_readme_badges_agree_with_the_shipped_versions(readme: Path) -> None:
    """AC-040: both READMEs carry a plugin badge AND an MCP-server badge.

    Derived from the manifests rather than pinned to literals, so a release
    bump moves one number in one place and this check follows it. Before this,
    the READMEs carried a plugin badge only -- the MCP server's version was
    published nowhere a reader could see it, so a plugin and a server that had
    drifted apart looked identical to one that had not.

    ``--`` is shields.io's escape for a literal hyphen in a badge label, so the
    server badge's label is written ``foundry--mcp`` and renders ``foundry-mcp``.
    """
    text = _read(readme)
    plugin_badge = f"badge/foundry-{_plugin_version()}-"
    server_badge = f"badge/foundry--mcp-{_server_version()}-"
    assert plugin_badge in text, (
        f"{_rel(readme)} has no shields badge reading "
        f"foundry {_plugin_version()} (looked for {plugin_badge!r}). The badge "
        f"must equal {_rel(PLUGIN_MANIFEST)}'s version."
    )
    assert server_badge in text, (
        f"{_rel(readme)} has no shields badge reading "
        f"foundry-mcp {_server_version()} (looked for {server_badge!r}). The "
        f"badge must equal {_rel(PYPROJECT)}'s version."
    )


def test_root_readme_describes_the_release_it_badges() -> None:
    """AC-040 / FR-027: the release section names the version it shipped.

    A badge bump with no release section leaves a reader able to see THAT the
    version moved and unable to see what moved with it.
    """
    heading = f"### foundry {_plugin_version()}"
    assert heading in _read(ROOT_README), (
        f"{_rel(ROOT_README)} has no {heading!r} release section. The badge "
        f"claims {_plugin_version()} shipped; the What's-new block has to say "
        f"what was in it."
    )


# ---------------------------------------------------------------------------
# Prose pins (D1-D15)
# ---------------------------------------------------------------------------
#
# ``(claim, requirement, path, phrase)``. The claim id is the pytest id, so a
# failure names the sentence that went missing rather than a row number. Every
# phrase is matched against the FLATTENED text -- see ``_flat``.

_PINS: tuple[tuple[str, str, Path, str], ...] = (
    # --- D1: the bounded lead-fix lane -------------------------------------
    # AC-024 / FR-014: the previous run's last defects were fixed by the lead
    # on a user's say-so, with no recorded bound on size and no record of who
    # authored them. Rule 2 now carries the bound in the same numbers the
    # server measures against.
    ("lane-latent-any-size", "FR-014", START_MD, "fix a `LATENT` defect of ANY size"),
    (
        "lane-live-limits",
        "FR-014",
        START_MD,
        "exactly ONE non-test file and at most 20 added-plus-deleted lines",
    ),
    (
        "lane-server-measures",
        "FR-016",
        START_MD,
        "by running `git show --numstat` on `fix_commit`",
    ),
    (
        "lane-server-writes-handoff",
        "AC-024",
        START_MD,
        "The SERVER writes the `lead_fix` handoff record",
    ),
    # --- D3: pointer dispatch ----------------------------------------------
    # FR-019: prompt TEXT in the dispatch is a copy the lead can edit, and
    # nothing afterwards shows what was actually delivered. The hash the agent
    # states back is what makes delivery checkable.
    ("dispatch-pointer", "FR-019", START_MD, "dispatch a pointer, not prompt text"),
    (
        "dispatch-hash-to-gates",
        "FR-019",
        START_MD,
        "pass it as `prompt_hash` to `Foundry-Accept-Casting` and to `Foundry-Fix`",
    ),
    (
        "dispatch-f3-blocks",
        "FR-019",
        START_MD,
        "returned `dispatch` block and `progress_protocol` block verbatim",
    ),
    # --- D2: escalation -----------------------------------------------------
    # AC-005: start.md documented escalation nowhere, so its entry, its budget
    # and its two exits were readable only from the orchestrator source.
    ("escalation-section", "AC-005", START_MD, "## ESCALATION"),
    (
        "escalation-entry",
        "AC-005",
        START_MD,
        "three consecutive server-counted cycles and still has an open instance",
    ),
    ("escalation-budget", "AC-005", START_MD, "one structural pass plus one retry"),
    (
        "escalation-latent-no-reset",
        "AC-005",
        START_MD,
        "**`LATENT` instances do not reset the count**",
    ),
    # ST-001 / ST-002: the exit REASON must sit beside the exit it names. A
    # bare `clean_cycles` pin would stay green on a table that swapped the two
    # rows, which is the one mistake that turns a cleared class into a lie
    # about how it cleared.
    ("escalation-exit-clean", "ST-001", START_MD, "| Clean cycles | `clean_cycles` |"),
    ("escalation-exit-budget", "ST-002", START_MD, "| Budget exhausted | `budget` |"),
    (
        "escalation-clearing-not-closing",
        "AC-005",
        START_MD,
        "Clearing ends escalation, never a defect.",
    ),
    (
        "escalation-override-not-exit",
        "AC-005",
        START_MD,
        "**`escalation-override` is not the exit rule.**",
    ),
    # --- D4: the tier briefing ---------------------------------------------
    (
        "tier-live-latent",
        "GI-001",
        START_MD,
        "`LIVE` when the stream drove the door and observed the wrong result",
    ),
    (
        "tier-latent-reproduction",
        "CT-001",
        START_MD,
        "the door refuses a `LATENT` filing without one",
    ),
    (
        "tier-security-denylist",
        "CT-003",
        START_MD,
        "A security-property claim can NEVER be `LATENT`",
    ),
    ("tier-denylist-class", "CT-003", START_MD, "`SECURITY_PROPERTY_CLAIM`"),
    # CT-008: unknown-tier is counted like LIVE, not waved through. A run that
    # read a pre-change record as "no tier, therefore not blocking" would walk
    # past exactly the defects nobody had classified yet.
    ("tier-unknown-blocks", "CT-008", START_MD, "counted exactly like `LIVE`"),
    # --- D5: inspect mode and Gate-then-Phase -------------------------------
    ("gate-then-phase", "ST-011", START_MD, "**Gate then Phase.**"),
    (
        "gate-then-phase-accepted",
        "OT-028",
        START_MD,
        "`Foundry-Phase` called DIRECTLY after `Foundry-Gate` is ACCEPTED",
    ),
    (
        "gate-then-phase-next-optional",
        "FR-044",
        START_MD,
        "A `Foundry-Next` between them is OPTIONAL",
    ),
    # GI-008 / GI-009: a mode computed lazily inside Foundry-Next is a decision
    # with no record, so a skipped Next means a cycle with no recorded width.
    ("inspect-next-only-reports", "GI-009", START_MD, "**`Foundry-Next` only REPORTS them.**"),
    ("inspect-rule-first", "GI-009", START_MD, "`first_of_phase`"),
    ("inspect-rule-final", "GI-009", START_MD, "`final_gate`"),
    ("inspect-rule-verifier", "GI-009", START_MD, "`verifier_touched`"),
    ("inspect-delta-test-cold", "GI-008", START_MD, "TEST runs full and cold, every time"),
    # D-136 instance / FR-024 / CT-016: lead rule 5 promised a TWO-ending run
    # ("F6 DONE or an error"), which is the ending vocabulary --max-cycles
    # replaced -- a HALTED stop is a SUCCESSFUL transition, so a lead reading
    # rule 5 would have had to call it one of the two endings it is not.
    (
        "rule-5-states-three-endings",
        "FR-024",
        START_MD,
        "it ends in exactly THREE ways",
    ),
    (
        "rule-5-halted-is-a-successful-transition",
        "CT-016",
        START_MD,
        "a SUCCESSFUL `Foundry-Phase` transition that writes the report",
    ),
    (
        "rule-5-halted-is-neither-of-the-other-two",
        "FR-024",
        START_MD,
        "never report it as a finished run and never report it as a refusal",
    ),
    # --- D6: Foundry-Spend --------------------------------------------------
    ("spend-section", "FR-021", START_MD, "## SPEND ACCOUNTING"),
    (
        "spend-after-every-agent",
        "FR-021",
        START_MD,
        "After EVERY agent completion, call `Foundry-Spend(",
    ),
    # GI-005: the usage block is real but undocumented, so a server-side parser
    # would break silently on a harness change and take the accounting with it.
    ("spend-no-server-parser", "GI-005", START_MD, "**The parser NEVER lives in the server.**"),
    (
        "spend-never-blocks",
        "FR-021",
        START_MD,
        "**A forgotten `Foundry-Spend` never blocks a gate.**",
    ),
    ("spend-no-dollars", "FR-021", START_MD, "No dollar figure appears anywhere"),
    # --- D7: Foundry-Report -------------------------------------------------
    (
        "report-generated-not-written",
        "FR-023",
        START_MD,
        "generates the run's report from the run's own ledgers",
    ),
    # GI-006: an omitted section reads as "this run had none of that" when the
    # truth is "nobody looked", which is why append is allowed and omit is not.
    (
        "report-append-not-omit",
        "GI-006",
        START_MD,
        "You MAY APPEND PROSE BELOW ANY SECTION. You may NEVER OMIT ONE.",
    ),
    (
        "report-done-refuses",
        "GI-006",
        START_MD,
        "refuses when the report is absent or a section is missing",
    ),
    # --- D7 / D-149: the F6 evidence step is SWEEP then STRIP ---------------
    # GI-002 sweeps the whole corpus "before ASSAY/NYQUIST/DONE", and this file
    # is the only thing that orders the two F6 acts against each other on the
    # guided path. It used to mandate the strip "after the report, before
    # Foundry-Phase('done')" -- with no gate in between -- which voided the
    # sweep the door was built to take. Driven at a run held at F5.5 with one
    # committed log whose command no longer reproduced: before the strip the
    # door refused naming that log; the mandated `git rm -r evidence/ && git
    # commit ... -- evidence/` was run verbatim; the identical door then passed,
    # because the strip is a COMMIT, it moved HEAD, and the re-sweep re-globbed
    # a directory that was no longer there. The sequence pin below is what keeps
    # the gate between the report and the strip.
    (
        "evidence-f6-sequence-gates-before-strip",
        "GI-002",
        START_MD,
        '`Foundry-Report` → `Foundry-Gate(phase="done")` → strip consumed '
        'evidence → `Foundry-Phase("done")`',
    ),
    (
        "evidence-gate-runs-before-the-rm",
        "GI-002",
        START_MD,
        "runs BEFORE the `git rm`, never after",
    ),
    # The pass has to OUTLIVE the strip commit or the ordering buys nothing:
    # HEAD moves, so the door can only be satisfied by a pass recorded against
    # the commit that still carried the corpus.
    (
        "evidence-pass-recorded-pre-strip",
        "GI-002",
        START_MD,
        "records that pass in `.evidence-swept-at-head.json` under "
        "`last_full_pass`",
    ),
    (
        "evidence-strip-first-refusal-token",
        "FR-042",
        START_MD,
        "`EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP`",
    ),
    # AC-014: the rung reported only `mismatches`, and a sweep that re-executed
    # NOTHING reports zero of them -- byte-identical to a clean whole-corpus
    # pass. The log count is the field that tells the two apart, so the lead
    # reading the checklist has to be told to read it.
    (
        "evidence-rung-names-the-log-count",
        "AC-014",
        START_MD,
        "`evidence_reproduces_at_head (logs=N, mismatches=M)`",
    ),
    # GI-002 again: a --nyquist run leaves F5.5 through `nyquist_done`, so prose
    # that named only `Foundry-Phase('done')` left the other terminal door
    # undocumented and the strip free to precede it.
    (
        "evidence-both-terminal-doors",
        "GI-002",
        START_MD,
        '`Foundry-Phase("nyquist_done")` out of F5.5 and '
        '`Foundry-Phase("done")` at F6 sweep the same corpus by the same rule',
    ),
    # --- D8: the --plugin-dir convention ------------------------------------
    # GI-004 / AC-029: all three surfaces carry it, because whichever one an
    # operator happens to read is the one that has to say it.
    (
        "plugin-dir-start",
        "GI-004",
        START_MD,
        "claude --plugin-dir <project_root>/plugins/foundry",
    ),
    (
        "plugin-dir-no-midrun-switch",
        "GI-004",
        START_MD,
        "**A mid-run server switch is NEVER attempted:**",
    ),
    (
        "plugin-dir-help",
        "AC-029",
        HELP_MD,
        "claude --plugin-dir <project_root>/plugins/foundry",
    ),
    (
        "plugin-dir-plugin-readme",
        "AC-029",
        PLUGIN_README,
        "claude --plugin-dir <project_root>/plugins/foundry",
    ),
    (
        "plugin-dir-root-readme",
        "GI-004",
        ROOT_README,
        "claude --plugin-dir <project_root>/plugins/foundry",
    ),
    (
        "plugin-dir-setup-help",
        "GI-004",
        SETUP_SH,
        "claude --plugin-dir <project_root>/plugins/foundry",
    ),
    # --- D9: --max-cycles and HALTED ----------------------------------------
    # CT-016 / FR-024: HALTED is reached by a SUCCESSFUL transition. Describing
    # it as a refusal, or as a finished run, are the two wrong readings.
    (
        "halted-transition-succeeds",
        "CT-016",
        START_MD,
        "**The `Foundry-Phase` call that would open a GRIND cycle beyond the cap SUCCEEDS.**",
    ),
    (
        "halted-not-done-start",
        "FR-024",
        START_MD,
        "**`HALTED` is a named terminal state distinct from `DONE`**",
    ),
    (
        "halted-not-done-help",
        "FR-024",
        HELP_MD,
        "`HALTED` is a named terminal state distinct from `DONE`",
    ),
    (
        "halted-not-done-plugin-readme",
        "FR-024",
        PLUGIN_README,
        "`HALTED` is a named terminal state distinct from `DONE`",
    ),
    ("halted-not-done-root-readme", "FR-024", ROOT_README, "`HALTED` is not `DONE`"),
    # --- D12: temper terminates ---------------------------------------------
    # AC-038 / FR-025: the skill said "Temper never declares itself done", so
    # F5 ended when a human said so rather than on the run's own evidence.
    (
        "temper-has-an-end",
        "AC-038",
        TEMPER_SKILL,
        "**But temper does have an end, and it is mechanical.**",
    ),
    (
        "temper-termination-rule",
        "AC-038",
        TEMPER_SKILL,
        "Temper is over when NO `LIVE` defect is open and EVERY escalated class is `CLEARED`.",
    ),
    ("temper-tier", "FR-025", TEMPER_SKILL, "**Every temper finding carries a `tier`"),
    (
        "temper-escalation-same-rule",
        "FR-025",
        TEMPER_SKILL,
        "escalation exits by the same rule everywhere",
    ),
    # --- D13: the rationale sections ----------------------------------------
    ("rationale-lane", "FR-014", LEAD_DISCIPLINE, "## Why the lead-fix lane is bounded"),
    ("rationale-pointer", "FR-019", LEAD_DISCIPLINE, "## Why dispatch is a pointer"),
    # --- D15: the setup script threads the cap ------------------------------
    # CT-016: MAX_CYCLES was parsed and echoed, and nothing told the lead to
    # pass it on -- the same gap `nyquist=` had, with the same consequence: the
    # flag is accepted at the command line and silently reaches nothing.
    ("setup-echoes-cap", "CT-016", SETUP_SH, "FOUNDRY_MAX_CYCLES=$MAX_CYCLES"),
    ("setup-threads-cap", "CT-016", SETUP_SH, "max_cycles=$MAX_CYCLES"),
    # --- GRIND cycle 2 -----------------------------------------------------
    # Each pin below answers a defect where prose that was TRUE when written
    # stood beside newer prose that contradicted it, or described a check the
    # shipped code does not perform. The class is stale-prose-survives-beside-
    # new-prose, and a substring pin is what stops the stale half surviving a
    # second time.
    #
    # D-024 / GI-002: GI-002's applies-to line names `commands/start.md F3`
    # and the word "sweep" appeared in start.md zero times. A refused
    # `inspect_start` therefore reached a lead with no instruction covering
    # it, and the only reading left was that the tool was broken.
    (
        "sweep-server-owns-the-boundary",
        "GI-002",
        START_MD,
        "**The SERVER sweeps the committed evidence at that same boundary, and a "
        "mismatch REFUSES the crossing.**",
    ),
    (
        "sweep-scope-is-delta-by-default",
        "GI-002",
        START_MD,
        "The scope is DELTA by default",
    ),
    (
        "sweep-lead-never-runs-it",
        "GI-002",
        START_MD,
        "You never run this sweep yourself and never report having run it",
    ),
    # The operator's first question after a refused transition is whether the
    # run moved; the second is whose job the re-capture is.
    (
        "sweep-refusal-does-not-advance",
        "GI-002",
        START_MD,
        "the cycle counter has NOT advanced, no INSPECT mode was recorded",
    ),
    (
        "sweep-owning-casting-recaptures",
        "GI-002",
        START_MD,
        "the casting that OWNS it re-captures it",
    ),
    # D-025 / FR-007: the tier paragraph copied the observation paragraph's
    # COUNT of the roster without its BINDING CLAUSE, so one file carried two
    # derivations of the same roster that disagreed about the members neither
    # ruling is written into.
    (
        "tier-roster-is-not-the-splits",
        "FR-007",
        START_MD,
        "Its roster is not the split's",
    ),
    (
        "tier-roster-binds-the-exempt-member",
        "FR-007",
        START_MD,
        "`agents/spec-test-deriver.md`, which has no `## Rules` block at all",
    ),
    (
        "tier-roster-never-derived-from-a-count",
        "FR-007",
        START_MD,
        "Never derive this roster from a COUNT",
    ),
    # D-138 / FR-011 / US-004: start.md taught the PRE-D-068 rule. `final_gate`
    # fired on `blocking == 0` once; it fires on two TRANSITION facts now (the
    # F2->F2 widening re-open, and a GRIND entered from ASSAY/TEMPER/NYQUIST
    # feedback), and the lead's own file promised a width the server had
    # stopped recording -- driven, a one-handler GRIND that start.md called
    # FULL/final_gate was recorded DELTA/delta.
    (
        "final-gate-is-two-transition-facts",
        "FR-011",
        START_MD,
        "on either of the TWO TRANSITION FACTS",
    ),
    (
        "final-gate-names-the-widening-re-open",
        "FR-011",
        START_MD,
        "this crossing is the F2\u2192F2 widening re-open",
    ),
    (
        "final-gate-reads-the-transition-not-the-ledger",
        "FR-011",
        START_MD,
        "**`final_gate` reads the transition, never the defect ledger.**",
    ),
    # AC-016 / FR-049: the close-out sent every clean cycle to `inspect_clean`,
    # which a DELTA cycle is refused at. The width decides the crossing, so the
    # routing sentence has to state the width.
    (
        "clean-delta-does-not-open-assay",
        "AC-016",
        START_MD,
        "**Zero blocking defects does not by itself open ASSAY",
    ),
    (
        "clean-delta-widens-from-f2",
        "FR-049",
        START_MD,
        "call `Foundry-Phase(phase='inspect_start')` AGAIN, from F2",
    ),
    (
        "both-assay-doors-check-the-width",
        "FR-049",
        START_MD,
        "**Both doors into ASSAY check the width by name, and both refuse a `DELTA` cycle:**",
    ),
    # US-007 / FR-021 (D-126): the lead was steered by a Foundry-Next field
    # that no longer exists, so the trigger could never fire. The replacement
    # steers by the roll-up `Foundry-Next` actually carries.
    (
        "context-steers-by-measured-spend",
        "FR-021",
        START_MD,
        "the only number the server has about that is a MEASURED one",
    ),
    (
        "context-no-field-estimates-lead-context",
        "US-007",
        START_MD,
        "**No field estimates YOUR remaining context, and none is coming:**",
    ),
    (
        "context-nothing-to-save-before-handover",
        "US-007",
        START_MD,
        "**`Foundry-Context` READS the run back on the far side of a handover; it writes nothing and saves nothing**",
    ),
    (
        "uncomputable-diff-is-full",
        "AC-036",
        START_MD,
        "an UNCOMPUTABLE GRIND diff",
    ),
    # D-027 / FR-018: three documentation surfaces said the preflight compares
    # the executing server's `__version__`. It compares the two plugin.json
    # versions; `__version__` is recorded as `server_version` and compared
    # against nothing.
    (
        "preflight-manifest-vs-manifest-start",
        "FR-018",
        START_MD,
        "It is plugin manifest against plugin manifest",
    ),
    (
        "preflight-server-version-uncompared-start",
        "FR-018",
        START_MD,
        "recorded as `server_version` and displayed, but never compared",
    ),
    (
        "preflight-manifest-vs-manifest-help",
        "FR-018",
        HELP_MD,
        "It is plugin manifest against plugin manifest",
    ),
    (
        "preflight-manifest-vs-manifest-plugin-readme",
        "FR-018",
        PLUGIN_README,
        "The comparison is plugin manifest against plugin manifest",
    ),
    # D-019 / AC-038: the file's OPENING line asserted a stricter ASSAY exit
    # than FR-006 defines, contradicting the termination rule the same file
    # states 195 lines later.
    (
        "temper-assay-exit-is-tier-aware",
        "AC-038",
        TEMPER_SKILL,
        "ASSAY passes when no `LIVE` defect and no unknown-tier defect is open",
    ),
    # D-019 / FR-030: the work-effort grade, by name, in the file that now
    # carries the tier rule.
    (
        "temper-suggestions-route-not-graded",
        "FR-030",
        TEMPER_SKILL,
        "**Never grade a suggestion by effort**",
    ),
    (
        "temper-tier-is-evidence-not-effort",
        "FR-030",
        TEMPER_SKILL,
        "which is evidence rather than effort",
    ),
    # --- GRIND cycle 3 -----------------------------------------------------
    # D-049 / CT-015: start.md's acceptance block told the lead the parameter
    # was an optional back-compat shim and that omitting it bypassed the whole
    # evidence block silently. The shipped tool refuses on absence, and the
    # SAME FILE's tools table already said "required" -- two statements in the
    # lead's own operating file disagreeing, with the false one describing a
    # bypass the server does not offer.
    (
        "accept-casting-commit-required",
        "CT-015",
        START_MD,
        "**`casting_commit` is REQUIRED, and it is what engages the evidence gate.**",
    ),
    (
        "accept-casting-commit-refusal",
        "CT-015",
        START_MD,
        "**Omit it and the acceptance is REFUSED naming the field**",
    ),
    # The return list carried the same optionality as a conditional clause.
    (
        "accept-casting-commit-provenance-always",
        "CT-015",
        START_MD,
        "populated on every success because `casting_commit` is required",
    ),
    # D-051 / FR-019: a REGRESSION of D-012, which was marked fixed with two of
    # its four surfaces untouched. Both rationale sections predated pointer
    # dispatch and still sent the lead to a field that is permanently null,
    # standing ABOVE the section that describes pointer dispatch correctly.
    (
        "dispatch-pointer-router-section",
        "FR-019",
        LEAD_DISCIPLINE,
        "which return a `dispatch` block naming that frozen file's path and its "
        "sha256 \u2014 never its text",
    ),
    (
        "dispatch-pointer-verbatim-section",
        "FR-019",
        LEAD_DISCIPLINE,
        "pass the `dispatch` block from `Foundry-Spawn-Teammate` or "
        "`Foundry-Cast-Wave` verbatim to the Agent tool",
    ),
    (
        "dispatch-pointer-help",
        "FR-019",
        HELP_MD,
        "gets back a **dispatch pointer** \u2014 the prompt file's path and its "
        "sha256, never its text",
    ),
    (
        "dispatch-pointer-plugin-readme",
        "FR-019",
        PLUGIN_README,
        "F1/F3 dispatch a pointer to the frozen file rather than its text",
    ),
    # --- GRIND cycle 5 ------------------------------------------------------
    # D-079 / D-091 / D-094 / D-095, all four filed against the ONE file that
    # instructs a filing without carrying the filing rules. The word-identity
    # sweep below is the structural half; these are the claims that sweep
    # cannot see, because they are about what temper says in its OWN voice
    # around the pasted block.
    #
    # D-094 / FR-030: the C3 routing sentence forbade routing by size and then
    # routed by "under 50 lines" in its own second clause -- the work-effort
    # axis surviving inside the sentence that abolishes it.
    (
        "temper-suggestions-no-size-threshold",
        "FR-030",
        TEMPER_SKILL,
        "Nothing here routes on how large a fix looks, and no line count appears "
        "in the rule",
    ),
    # D-095 / AC-009: temper banned three of the six names, and only on a
    # suggestion. The defect-level ban now arrives with the shared block; this
    # pins the suggestion-level one to the SAME six, so the two cannot drift
    # into disagreeing about which words are the axis.
    (
        "temper-suggestion-ban-names-all-six",
        "AC-009",
        TEMPER_SKILL,
        "no `minor`, no `major`, no `critical`, no `severity`, no `priority`, "
        "no `impact`, and no fresh spelling for the same axis next cycle",
    ),
    # D-091 / CT-002: temper's file never told it to set `class`, and every
    # filing instruction in it named only `source`. Both doors refuse a filing
    # with an empty class, and Foundry-Sync refuses the WHOLE batch on one.
    (
        "temper-filing-instruction-names-the-fields",
        "CT-002",
        TEMPER_SKILL,
        "each carrying `tier`, `defect_class`, `target_kind` and — on a "
        "`LATENT` filing — `reproduction_attempted`, per the filing rules in "
        "`## Key Constraints`",
    ),
    # D-054 follow-on / FR-007: the roster sentence read as "the agent files",
    # which is a roster short by one now that sight files through the same door
    # from under `skills/`.
    (
        "tier-roster-admits-a-skill",
        "FR-007",
        START_MD,
        "and into the `#### Filing rules` block of `skills/sight/SKILL.md`",
    ),
)


@pytest.mark.parametrize(
    ("requirement", "path", "phrase"),
    [(req, path, phrase) for _, req, path, phrase in _PINS],
    ids=[claim for claim, _, _, _ in _PINS],
)
def test_lead_prose_pin(requirement: str, path: Path, phrase: str) -> None:
    """The pinned sentence is still in the file that has to carry it."""
    assert phrase in _flat(path), (
        f"{_rel(path)} no longer contains {phrase!r} ({requirement}). If the "
        f"wording genuinely had to change, update this pin to the new sentence "
        f"-- do not delete it, and never soften the sentence to keep the pin "
        f"green."
    )


# ---------------------------------------------------------------------------
# Negative pins: claims that had to GO
# ---------------------------------------------------------------------------


#: Spellings a ruling RETIRED, mapped to what start.md says instead. A positive
#: pin cannot see a retired sentence that survives BESIDE its replacement --
#: which is the whole shape of the stale-prose class -- so the retirement is
#: asserted as an absence, per spelling, in the file that has to have dropped it.
_RETIRED_START_MD_SPELLINGS: tuple[tuple[str, str, str], ...] = (
    # D-126 / US-007: `Foundry-Next` returned no such key. Driven -- three spend
    # records, and the response carried action, details, directives, display,
    # executing_server, instructions, phase and spend, so the trigger this
    # sentence gave the lead could never fire. The steer is the measured
    # `spend` roll-up now.
    (
        "estimated_usage",
        "FR-021",
        "the CONTEXT MANAGEMENT steer names the `spend` roll-up Foundry-Next "
        "really carries, not a field that was deleted from the server",
    ),
    # D-126 / US-007: `foundry_get_context` is a READER ("Return all foundry
    # state in one call. Use after compaction or session start."). Calling it
    # before a handover preserved nothing; every tool call had already written
    # the run dir.
    (
        "save state via `Foundry-Context`",
        "US-007",
        "Foundry-Context reads the run back after a handover -- it writes "
        "nothing, so there is no state for it to save first",
    ),
    # D-138 / FR-011: the pre-D-068 `final_gate` framing. `blocking == 0` was
    # the proxy, and removing it is what made DELTA reachable at all.
    (
        "on either of TWO PROXIES",
        "FR-011",
        "final_gate fires on two TRANSITION facts -- the F2->F2 widening "
        "re-open, or a GRIND entered from ASSAY/TEMPER/NYQUIST feedback",
    ),
    (
        "The proxies are what fires, not the intent.",
        "FR-011",
        "the sentence it qualified is gone; what fires is named directly",
    ),
    # D-138 / AC-016: the close-out routed every clean cycle to `inspect_clean`,
    # which refuses a DELTA cycle naming final_gate. Driven -- a cleared ledger
    # plus a one-handler GRIND recorded DELTA/delta, and inspect_clean refused.
    (
        'Zero defects → `Foundry-Phase("inspect_clean")` → F4',
        "AC-016",
        "the recorded width decides the crossing: FULL goes to inspect_clean, "
        "DELTA goes back through inspect_start from F2",
    ),
    # D-136 instance / FR-024: the two-ending run. A --max-cycles stop reaches
    # HALTED through a transition that returns ok, so "an error stops it" was
    # the only vocabulary rule 5 gave the lead for an ending that is not an
    # error. This spelling is row seven of the run-wide retired-mechanism
    # registry in test_orchestrator_gates.py; the pin here is start.md's own.
    (
        "runs until F6 DONE or an error stops it",
        "FR-024",
        "a run ends three ways: F6 DONE, a HALTED --max-cycles stop reached by "
        "a successful transition, or an error",
    ),
)


@pytest.mark.parametrize(
    ("spelling", "requirement", "instead"),
    _RETIRED_START_MD_SPELLINGS,
    ids=[s for s, _, _ in _RETIRED_START_MD_SPELLINGS],
)
def test_start_md_dropped_the_retired_spelling(
    spelling: str, requirement: str, instead: str
) -> None:
    """The retired sentence is GONE, not sitting beside its replacement.

    Every instance of the stale-prose class this run escalated arrived the same
    way: a new paragraph was written and the old one was left two paragraphs
    up, so the file stated a live rule and a dead one with equal authority and
    the lead had no way to tell which was which.
    """
    assert spelling not in _flat(START_MD), (
        f"{_rel(START_MD)} still contains {spelling!r} ({requirement}), which "
        f"names a mechanism the server no longer has. Instead: {instead}. "
        f"Delete the retired sentence -- do not leave it beside the one that "
        f"replaced it."
    )


def test_temper_no_longer_claims_a_completion_check_nobody_performs() -> None:
    """AC-038 / FR-025: anti-pattern 7 asserted a check that does not exist.

    "The orchestrator rejects completion if CRACKED domains exist without fix
    attempts or STUCK status" -- nothing anywhere reads domain status, so the
    sentence described a guard that would never fire and told the auditor it
    was covered. What actually holds the run is the tier-aware gates.
    """
    flat = _flat(TEMPER_SKILL)
    assert "The orchestrator rejects completion" not in flat, (
        f"{_rel(TEMPER_SKILL)} still claims the orchestrator rejects completion "
        f"on domain status. No such check exists. State what actually holds "
        f"the run -- the tier-aware gates -- or remove the claim."
    )
    assert "tier-aware gates" in flat, (
        f"{_rel(TEMPER_SKILL)} removed the false completion claim without "
        f"replacing it. Anti-pattern 7 has to say what really blocks a run."
    )


# ---------------------------------------------------------------------------
# The shared filing-rule block temper carries word-identically (D-079 / D-091 /
# D-095)
# ---------------------------------------------------------------------------
#
# temper is a `vocab.DEFECT_SOURCE_IDS` member whose own file instructs three
# `Foundry-Defect` calls, so it meets the same doors every stream meets. For
# four cycles it met them carrying a PARAPHRASE: it stated the security rule as
# "A security-property claim can NEVER be `LATENT`" without naming
# `SECURITY_PROPERTY_CLAIM`, the token the refusal actually reports; it never
# told itself to set `class`, which both doors refuse an empty one of and which
# `Foundry-Sync` refuses a whole BATCH over; and it banned three of the six
# work-effort names on a suggestion and none of them on a defect. Each cycle
# closed the clause that had been filed and left the paraphrase, and the class
# came back.
#
# So this sweep pins the SHAPE rather than the clauses: the bullets temper
# carries must be BYTE-IDENTICAL with `agents/assayer.md`'s, which is where
# every filing surface's copy comes from. A paraphrase fails here whatever
# tokens it happens to contain, and a rule assayer gains later fails here until
# temper gains it too -- which is the half a token sweep structurally cannot
# see.

#: The bullets every defect-filing surface carries word-identically, keyed by
#: the bolded imperative each opens with. Order matters and is asserted below:
#: the no-severity bullet points forward at "the `tier` axis the next rule
#: makes required", and the `target_kind` bullet points back at "that refusal"
#: and "the split above" -- both of which are the comment-prose bullet. A block
#: that drops the middle bullet keeps two dangling references, which is the
#: state `skills/sight/SKILL.md` is in today.
_SHARED_FILING_BULLETS = (
    "- **Name the class when instances share a root cause.**",
    "- **No severity classification.**",
    "- **Set `tier` on every filing;",
    "- **Comment-prose findings are observations, not defects.**",
    "- **Declare `target_kind` on every filing.**",
)


def _bullet(path: Path, head: str) -> str:
    """The one line in `path` opening with `head`, failing loudly on 0 or many."""
    hits = [line for line in _read(path).splitlines() if line.startswith(head)]
    assert len(hits) == 1, (
        f"{_rel(path)} carries {len(hits)} bullets opening {head!r}, expected "
        f"exactly 1. Two copies of a shared rule is the drift this sweep "
        f"exists to catch; zero means the rule was dropped or reworded."
    )
    return hits[0]


@pytest.mark.parametrize("head", _SHARED_FILING_BULLETS)
def test_temper_carries_the_shared_filing_bullet_verbatim(head: str) -> None:
    """D-079 / D-091 / D-095: temper's copy is the source's copy, byte for byte."""
    source = _bullet(ASSAYER, head)
    assert _bullet(TEMPER_SKILL, head) == source, (
        f"{_rel(TEMPER_SKILL)}'s {head!r} bullet has drifted from "
        f"{_rel(ASSAYER)}'s. These are shared rules: the doors report ONE "
        f"refusal per violation, so a surface that words the rule differently "
        f"teaches its stream a vocabulary the refusal will not use. Copy the "
        f"line from {_rel(ASSAYER)} rather than re-wording it here, and if the "
        f"rule itself is wrong, change it at the source and re-copy."
    )


def test_the_shared_filing_bullets_are_in_the_order_their_references_need() -> None:
    """The back-references only resolve in one order, and sight proves it.

    `skills/sight/SKILL.md` carries four of these five -- it drops the
    comment-prose bullet and keeps the `target_kind` bullet that says "that
    refusal is not automatic" and "the split above did nothing", leaving both
    pointing at nothing. temper's copy must not inherit that: this asserts the
    antecedent is present AND above the bullet that refers to it.
    """
    text = _read(TEMPER_SKILL)
    positions = [text.index(_bullet(TEMPER_SKILL, head)) for head in _SHARED_FILING_BULLETS]
    assert positions == sorted(positions), (
        f"{_rel(TEMPER_SKILL)}'s shared filing bullets are out of order. "
        f"Expected {list(_SHARED_FILING_BULLETS)}: the no-severity bullet names "
        f"'the `tier` axis the next rule makes required', so the tier bullet "
        f"must follow it, and the `target_kind` bullet's 'that refusal' and "
        f"'the split above' are the comment-prose bullet, so it must precede."
    )


def test_start_md_does_not_claim_next_is_required_between_gate_and_phase() -> None:
    """ST-011 / FR-044: the gate no longer unlinks the ordering token.

    A lead that believes a `Foundry-Next` is mandatory between the two calls
    inserts one it does not need; a lead that believes the transition will be
    REFUSED without it treats a successful path as broken. Neither reading may
    survive in the prose.
    """
    flat = _flat(START_MD)
    assert "A `Foundry-Next` between them is OPTIONAL" in flat, (
        f"{_rel(START_MD)} must state that Foundry-Next between Gate and Phase "
        f"is optional; without it the lead cannot tell the ordering is legal."
    )
    for wrong in (
        "must call `Foundry-Next` between",
        "requires a `Foundry-Next` between",
    ):
        assert wrong not in flat, (
            f"{_rel(START_MD)} says {wrong!r}. Gate then Phase is accepted "
            f"directly; a Foundry-Next between them is optional (ST-011)."
        )


# ---------------------------------------------------------------------------
# Structural checks
# ---------------------------------------------------------------------------


def test_the_report_step_names_every_required_section() -> None:
    """GI-006 / FR-023: the section roster is DERIVED from vocab, not typed.

    ``Foundry-Phase('done')`` refuses naming a missing section, so the lead
    needs the section NAMES to act on that refusal. Derived from
    ``REPORT_REQUIRED_SECTIONS`` so a section added there without a row here
    fails, rather than leaving the lead with a refusal naming a key the
    protocol never mentions.
    """
    from foundry_mcp.schemas import vocab

    text = _read(START_MD)
    missing = [s for s in vocab.REPORT_REQUIRED_SECTIONS if f"`{s}`" not in text]
    assert not missing, (
        f"{_rel(START_MD)}'s F6 report step does not name {missing}. Every "
        f"member of REPORT_REQUIRED_SECTIONS needs a row: the done gate "
        f"refuses by these names and the lead has to recognise them."
    )


def test_the_f6_sequence_sweeps_before_it_strips() -> None:
    """GI-002 / D-149: the ORDER is the guard, and no phrase pin can see it.

    Every sentence this section needs can be present while the two acts sit in
    the wrong order, and in the wrong order they cancel: the strip is a commit,
    so it moves ``HEAD``, and the whole-corpus sweep the terminal door takes
    then re-globs a directory that is no longer there. Driven on a run held at
    F5.5 with one committed log whose command no longer reproduced -- the door
    refused naming that log, the mandated ``git rm -r evidence/ && git commit
    ... -- evidence/`` was run verbatim, and the identical door passed. A
    sweep that re-executed nothing reports the same zero mismatches an earned
    pass reports, so the pass was indistinguishable from the refusal it
    replaced.

    Asserted as POSITIONS, in the two places the order is actually stated: the
    one-line F6 sequence, and the mandate paragraph that tells the lead when to
    run the ``git rm``. A reorder that kept every pinned sentence would fail
    here and nowhere else.
    """
    text = _read(START_MD)
    parts = text.split("### F6: DONE", 1)
    assert len(parts) == 2, (
        f"{_rel(START_MD)} has no `### F6: DONE` section. The F6 ordering "
        f"rules are asserted inside it; if the heading was renamed, retarget "
        f"this test -- do not drop it."
    )
    section = parts[1].split("\n## ", 1)[0]

    line = next(
        (ln for ln in section.splitlines() if ln.startswith("Shut down all teammates")),
        None,
    )
    assert line is not None, (
        f"{_rel(START_MD)}'s F6 section no longer opens with the one-line "
        f"`Shut down all teammates ...` sequence. That line is where the lead "
        f"reads the order of the whole phase."
    )
    steps = (
        "`Foundry-Report`",
        '`Foundry-Gate(phase="done")`',
        "strip consumed evidence",
        '`Foundry-Phase("done")`',
    )
    at = [line.find(step) for step in steps]
    missing = [step for step, pos in zip(steps, at) if pos == -1]
    assert not missing, (
        f"{_rel(START_MD)}'s F6 sequence line does not name {missing}. The "
        f"gate is the step that re-executes the committed corpus and records "
        f"the pass; without it in the sequence the strip runs first and the "
        f"door is asked about a corpus that is gone."
    )
    assert at == sorted(at), (
        f"{_rel(START_MD)}'s F6 sequence runs {steps} in the order "
        f"{[s for _, s in sorted(zip(at, steps))]}. It must gate BEFORE it "
        f"strips: the strip is a commit, it moves HEAD, and a sweep taken "
        f"after it re-executes nothing and reports zero mismatches -- a pass "
        f"that proves nothing."
    )

    gate = section.find('`Foundry-Gate(phase="done")` runs BEFORE')
    strip = section.find("git rm -r evidence/")
    assert gate != -1, (
        f"{_rel(START_MD)}'s evidence-lifecycle step no longer states that "
        f"`Foundry-Gate(phase=\"done\")` runs before the `git rm`. The "
        f"sequence line alone is a summary; this is the mandate."
    )
    assert strip != -1, (
        f"{_rel(START_MD)}'s evidence-lifecycle step no longer carries the "
        f"`git rm -r evidence/` strip command."
    )
    assert gate < strip, (
        f"{_rel(START_MD)} states the strip command before it states that the "
        f"gate runs first. A lead reads this paragraph top to bottom and runs "
        f"what it reaches; the sweep must be the thing it reaches first."
    )


@pytest.mark.parametrize(
    "heading",
    ("## Why the lead-fix lane is bounded", "## Why dispatch is a pointer"),
)
def test_new_rationale_sections_keep_the_house_shape(heading: str) -> None:
    """D13: every section of lead-discipline.md is Why -> failure mode -> fix.

    The file's whole value is that a reader who hits a rule can find, in one
    predictable shape, the concrete failure it answers. A section that states
    only the rule restates start.md and earns nothing.
    """
    text = _read(LEAD_DISCIPLINE)
    start = text.find(heading)
    assert start != -1, f"{_rel(LEAD_DISCIPLINE)} has no {heading!r} section."
    end = text.find("\n## ", start + 1)
    section = text[start : end if end != -1 else len(text)]
    for marker in ("**The failure mode.**", "**The fix.**"):
        assert marker in section, (
            f"{_rel(LEAD_DISCIPLINE)}'s {heading!r} section has no {marker} "
            f"paragraph. Every section in this file carries both."
        )


def test_start_md_tools_table_rows_are_two_column() -> None:
    """AC-005: the table the completeness check reads is really a table.

    A tool name that reached the section as prose rather than as a row would
    satisfy the substring comparison above while leaving the reference table
    incomplete, so the row shape is checked separately from membership.
    """
    rows = re.findall(r"^\| `([^`]+)` \|", _tools_table(), re.MULTILINE)
    registered = _registered_tool_names()
    as_rows = {name for name in rows if name in registered}
    assert as_rows == registered, (
        f"{_rel(START_MD)}'s tools table reaches these tools as prose rather "
        f"than as a `| `Tool` | When |` row: {sorted(registered - as_rows)}"
    )


# ---------------------------------------------------------------------------
# The tier-rule roster start.md claims (D-025 / FR-007)
# ---------------------------------------------------------------------------
#
# start.md derives the INSPECT stream roster TWICE -- once for the observation
# split and once for the tier rule -- and the two derivations disagreed. The
# split paragraph named its four files AND bound the two roster members it is
# not written into; the tier paragraph copied the COUNT and dropped the
# binding, so the same file said two different things about who the ruling
# reaches on a fresh checkout.
#
# The pins above hold the binding clause in place. This test holds the other
# half: that every agent file start.md CLAIMS carries the tier rule really
# does. The list is parsed out of start.md rather than typed here, so the
# assertion tracks the prose instead of becoming a third copy free to drift
# from both.

#: The sentence in start.md's F2 roster whose backticked prose paths are the
#: tier rule's fresh-checkout roster. Anchored on both ends so a nearby
#: paragraph's agent references cannot leak into the derivation.
_TIER_ROSTER_RE = re.compile(
    r"the tier rule is written into the `## Rules` block of (.+?)\. "
    r"The one roster member it is not written into is `([^`]+)`"
)

#: A roster member's path, in either spelling a filing surface can have. The
#: alternation is the D-054 lesson arriving on the LEAD's side of the protocol:
#: `test_protocol_prose.py` derives its filing corpus over BOTH `agents/*.md`
#: and `skills/*/SKILL.md` because a surface joins it by NAMING A FILING DOOR,
#: not by living in `agents/`. This pattern matched only the first spelling, so
#: `skills/sight/SKILL.md` -- a first-class member of that corpus, carrying the
#: tier, class and `target_kind` rules word-identically -- could not be named
#: in the roster sentence at all: adding it parsed the list one member short
#: and the sweep below then read a roster that no longer matched the corpus.
_ROSTER_PATH_RE = re.compile(r"`(agents/[^`]+\.md|skills/[^`]+/SKILL\.md)`")


def _claimed_tier_roster() -> tuple[list[str], str]:
    """``(files start.md says carry the tier rule, the file it says does not)``."""
    match = _TIER_ROSTER_RE.search(_flat(START_MD))
    assert match is not None, (
        f"{_rel(START_MD)}'s F2 roster no longer names which agent files carry "
        f"the tier rule, or no longer names the roster member it is not "
        f"written into. D-025 was exactly that omission: a roster stated as a "
        f"count, with nothing saying who the count leaves out. Restore both "
        f"halves of the sentence rather than relaxing this pattern."
    )
    return _ROSTER_PATH_RE.findall(match.group(1)), match.group(2)


def test_the_tier_roster_derivation_is_not_vacuous() -> None:
    """A roster parse that silently matched nothing would pass every check below.

    The same guard the tools-table derivation carries, for the same reason: an
    empty derived set makes an `all()` over it trivially true, so the parser
    has to prove it found something before its findings mean anything.
    """
    named, exempt = _claimed_tier_roster()
    assert len(named) >= 4, (
        f"{_rel(START_MD)}'s tier-rule roster parsed to {named!r}. The four "
        f"defect-filing stream agents are the floor; a parse this small means "
        f"the sentence changed shape and this test is now checking nothing."
    )
    # The widened alternation needs its own vacuity guard: a pattern that admits
    # `skills/*/SKILL.md` and never matches one is indistinguishable from the
    # agent-only pattern it replaced, which is the state D-054 left the roster in.
    assert any(rel.startswith("skills/") for rel in named), (
        f"{_rel(START_MD)}'s tier-rule roster parsed to {named!r}, which names "
        f"no `skills/*/SKILL.md` member. A filing surface joins this roster by "
        f"naming a filing door, not by living in `agents/` -- and a roster that "
        f"lists only agents is the one this sentence's own closing clause warns "
        f"against reading as a count."
    )
    assert exempt.startswith("agents/"), (
        f"{_rel(START_MD)} names {exempt!r} as the exempt roster member, which "
        f"is not an agent file path."
    )


def test_every_agent_start_md_names_really_carries_the_tier_rule() -> None:
    """FR-007 / D-025: start.md's claim about the roster files must be true.

    "It is in force on a fresh checkout" is a claim about files start.md does
    not own. If the ruling is missing from one of them, the lead is told no
    per-run directive is needed and the stream files without a tier anyway --
    which the door then refuses mid-INSPECT, with the protocol's own prose as
    the reason the lead trusted it would not.

    A roster member under `skills/` is checked over the WHOLE file rather than
    over a `## Rules` block, because a skill carries no such heading: sight
    states the rules under `#### Filing rules` and temper under
    `## Key Constraints`. Demanding one heading of both populations is what
    made the roster agent-only in the first place, and the substance -- the
    tier the door refuses without -- is what the claim is actually about.
    """
    named, exempt = _claimed_tier_roster()
    for rel in named:
        member = FOUNDRY_ROOT / rel
        assert member.is_file(), (
            f"{_rel(START_MD)} names {rel} as carrying the tier rule, but that "
            f"file does not exist."
        )
        body = member.read_text(encoding="utf-8")
        if member.name == "SKILL.md":
            scope, where = body, "file"
        else:
            rules = body.split("## Rules", 1)
            assert len(rules) == 2, (
                f"{_rel(member)} has no `## Rules` block, so {_rel(START_MD)} "
                f"is wrong to list it among the agent files that carry the tier "
                f"rule there. Move it to the exemption clause instead."
            )
            scope, where = rules[1], "`## Rules` block"
        assert "LATENT" in scope, (
            f"{_rel(member)}'s {where} does not state the tier rule, "
            f"but {_rel(START_MD)} says it does. Either the rule was dropped "
            f"from the roster file -- restore it -- or start.md must stop "
            f"claiming it and bind the file in the exemption clause. Never fix "
            f"this by deleting the claim's file list; a roster stated as a bare "
            f"count is the defect this test exists for."
        )

    exempt_file = AGENTS / Path(exempt).name
    assert exempt_file.is_file(), (
        f"{_rel(START_MD)} names {exempt} as the exempt roster member, but that "
        f"file does not exist."
    )
    assert exempt not in named, (
        f"{_rel(START_MD)} lists {exempt} as both carrying the tier rule and "
        f"exempt from it."
    )


# ---------------------------------------------------------------------------
# D-051 -- the pointer-dispatch claim, swept over the whole lead-prose corpus
# ---------------------------------------------------------------------------

#: Every prose file this module owns. The two sweeps below run over ALL of
#: them rather than over the file a defect was filed against, because D-051 is
#: what happens when a fix touches only the surface named in the filing: D-012
#: was marked fixed with two of its four surfaces untouched, and the stale half
#: came back one cycle later. A per-file pin cannot catch a claim that migrates.
_LEAD_PROSE_CORPUS: tuple[Path, ...] = (
    START_MD,
    HELP_MD,
    LEAD_DISCIPLINE,
    TEMPER_SKILL,
    PLUGIN_README,
    ROOT_README,
)

#: Split flattened prose on sentence boundaries. A qualifier only qualifies the
#: sentence it sits in -- the D-079 lesson from ``test_protocol_prose.py``'s
#: liveness rows -- so both sweeps below assert per SENTENCE, never per file.
#: The lookbehind requires the following space, so "F0.5" and "sha256." inside
#: a clause do not split.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?]) ")

#: Ways a file can claim a spawn tool hands back the prompt TEXT. Under pointer
#: dispatch that is only true with ``full_prompt=true``, so a sentence making
#: the claim must name the flag or it is describing the pre-pointer protocol.
_TEXT_RETURN_CLAIMS = (
    "returns the text",
    "return the text",
    "returns the prompt text",
    "returns the prompt back",
    "gets the prompt back",
    "gets the pre-authored prompt back",
)


def _sentences(path: Path) -> list[str]:
    return _SENTENCE_SPLIT.split(_flat(path))


def test_no_owned_prose_sends_the_lead_to_the_prompt_field() -> None:
    """D-051 / FR-019: the `prompt` field is null, so naming it must say so.

    ``foundry_spawn.py`` returns ``"prompt": None`` unless ``full_prompt=True``
    is passed. ``references/lead-discipline.md`` told the lead to "pass the
    `prompt` field from `Foundry-Spawn-Teammate` verbatim to the Agent tool",
    which delivers an empty prompt to the teammate and leaves no trace that it
    did. The rule is therefore not "never mention the field" -- start.md has to
    mention it to warn the lead off -- but "never mention it without saying it
    comes back null".
    """
    carriers: list[tuple[Path, str]] = []
    for path in _LEAD_PROSE_CORPUS:
        for sentence in _sentences(path):
            if "`prompt`" not in sentence:
                continue
            carriers.append((path, sentence))
            assert "null" in sentence, (
                f"{_rel(path)} names the `prompt` field without saying it is "
                f"null: {sentence!r}. Pointer dispatch returns `prompt: null` "
                f"unless full_prompt=true, so a lead following this sentence "
                f"hands the agent an empty prompt. Say what to pass instead -- "
                f"the `dispatch` block -- and say the field is null."
            )
    assert carriers, (
        "No file in the lead-prose corpus mentions the `prompt` code span at "
        "all, so this sweep asserted nothing. Either the warning that the "
        "field is null was deleted from start.md and lead-discipline.md -- "
        "restore it -- or the span is now written some other way and this "
        "check no longer matches it. Do not delete the sweep to make it pass."
    )


def test_no_owned_prose_says_a_spawn_tool_hands_back_the_prompt_text() -> None:
    """D-051 / FR-019: only `full_prompt=true` returns prompt text.

    The regressed sentence was "The lead at F1/F3 calls
    `Foundry-Spawn-Teammate` which reads the file and returns the text" -- true
    before pointer dispatch, false after, and sitting two sections above the
    one that describes pointer dispatch correctly. A claim that the tool hands
    back text is legitimate ONLY where it names the debugging flag that makes
    it so.
    """
    for path in _LEAD_PROSE_CORPUS:
        for sentence in _sentences(path):
            claim = next((c for c in _TEXT_RETURN_CLAIMS if c in sentence), None)
            if claim is None:
                continue
            assert "full_prompt" in sentence, (
                f"{_rel(path)} says {claim!r} without naming full_prompt: "
                f"{sentence!r}. The spawn tools return a `dispatch` block "
                f"naming a path and a sha256; the text comes back only with "
                f"full_prompt=true, which exists for debugging. State the "
                f"pointer, or name the flag."
            )
