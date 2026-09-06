"""Pins the LEAD-facing half of the protocol: the three command files, the
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
RESUME_MD = COMMANDS / "resume.md"
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


#: The PLUGIN badge a README publishes, as shields.io writes it. ``--`` is
#: shields' escape for a literal hyphen, so the server badge is written
#: ``badge/foundry--mcp-1.9.0-`` and cannot match here: the character after
#: ``foundry-`` must be a digit.
_PLUGIN_BADGE_RE = re.compile(r"badge/foundry-(\d+\.\d+\.\d+)-")


def _badged_plugin_version(readme: Path) -> str:
    """The plugin version THIS readme's own badge claims.

    D-211 / AC-040: derived from the file under test rather than from the
    manifest, so the pin below binds each README's release prose to the badge
    printed at the top of the same file. The manifest-to-badge half of the
    chain is already pinned per README by
    ``test_readme_badges_agree_with_the_shipped_versions`` above, so reading
    the badge here closes the chain manifest -> badge -> prose while leaving
    exactly one derivation of the version in this module. A typed constant, or
    a second read of the manifest, would be a copy free to drift from the badge
    it is supposed to be checking.
    """
    found = sorted(set(_PLUGIN_BADGE_RE.findall(_read(readme))))
    assert len(found) == 1, (
        f"{_rel(readme)} publishes {len(found)} distinct plugin badge versions "
        f"{found}; expected exactly one. Zero means the badge row lost its "
        f"plugin badge or changed shape and this derivation no longer sees it; "
        f"more than one means a bump moved one badge and left another behind. "
        f"Fix the badges -- until there is one version, no pin can say which "
        f"release the prose owes."
    )
    return found[0]


@pytest.mark.parametrize("readme", (PLUGIN_README, ROOT_README), ids=_rel)
def test_readme_describes_the_release_it_badges(readme: Path) -> None:
    """AC-040 / FR-027: the release section names the version it badges.

    A badge bump with no release section leaves a reader able to see THAT the
    version moved and unable to see what moved with it.

    D-211: this ran over the ROOT readme only. The plugin README duly carried
    ``foundry-4.10.0`` and ``foundry--mcp-1.9.0`` shields while its one release
    section stayed headed "What's new since v4.2.0" and enumerated the
    v4.2.0-era additions -- EVID-01, EVID-02, TEST-01, INTENT-01 -- so a reader
    of the badged file learned nothing about the release it badged. The spec's
    File Change Map names ``plugins/foundry/README.md`` and ``README.md`` in
    one row for "release section and badges", so both are parametrized here.
    ``commands/help.md`` shares that row for the flag semantics only: it
    publishes no badge and carries no release section, so there is no version
    in it for this derivation to read.
    """
    version = _badged_plugin_version(readme)
    heading = f"### foundry {version}"
    assert heading in _read(readme), (
        f"{_rel(readme)} has no {heading!r} release section. Its own badge "
        f"claims {version} shipped; the What's-new block has to say what was "
        f"in it. Do not narrow this pin back to one README -- the plugin "
        f"README is exactly where a stale section survived a badge bump."
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
    # D-157 / ST-001: "two consecutive cycles" is the whole rule only if the
    # reader already knows which cycle counting STARTS from. The arm skips every
    # crossing at or before `escalated_at_cycle`, so a class that has just
    # escalated is THREE crossings from its clean exit, and prose that stopped
    # at "two" authored the same off-by-one the still-escalated hint printed:
    # the hint derived its distance as LIVE_CLEAN_CYCLES_TO_CLEAR minus
    # live_clean_cycles, consulting neither the guard nor the counted list,
    # while the arm it named applied both. Two derivations of one number, and
    # the lead was handed the one the arm would not honour.
    (
        "escalation-clean-arm-skips-the-escalation-cycle",
        "ST-001",
        START_MD,
        "skips any cycle at or before the class's `escalated_at_cycle`",
    ),
    (
        "escalation-clean-arm-counts-three-crossings",
        "ST-001",
        START_MD,
        "the exit is THREE crossings away, not two",
    ),
    (
        "escalation-distance-is-crossings-not-arithmetic",
        "ST-001",
        START_MD,
        "never as `2` minus `live_clean_cycles`",
    ),
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
        "Every generated section ships, and you may NEVER OMIT ONE.",
    ),
    # D-228 / D-230 / GI-006, lead ruling of GRIND cycle 27. The seal no longer
    # merges the lead's prose back into the position it was typed at -- doing
    # that needs a line-granular value comparison, and a generated row whose
    # VALUE moved between two generations is indistinguishable from a line the
    # lead typed, so the old merge re-emitted stale generated rows as "prose
    # you appended" and a sealed report ended with two contradictory values for
    # one question, attributed to the operator. The seal is coarser now: it
    # carries what lies OUTSIDE the generated skeleton and nothing else. That
    # makes WHERE the lead types load-bearing, so start.md has to say it. A
    # lead still following "append below any section" types inside a generated
    # body and the next terminal transition eats it, silently.
    (
        "report-append-under-your-own-heading",
        "GI-006",
        START_MD,
        "Append your own prose UNDER YOUR OWN `## ` HEADING",
    ),
    (
        "report-seal-carries-into-one-trailing-section",
        "GI-006",
        START_MD,
        "copied VERBATIM into one trailing `## Lead notes (carried by the "
        "seal)` section appended after every generated section",
    ),
    # The COST of the coarser rule, stated where the lead reads it. Naming the
    # surviving positions without naming what does not survive leaves the lead
    # to discover the loss from a diff of the archived report.
    (
        "report-inside-a-section-is-regenerated-away",
        "GI-006",
        START_MD,
        "Prose typed INSIDE a generated section's body is NOT carried \u2014 "
        "it is regenerated away.",
    ),
    # Both READMEs carry the same tools-table row, and both promised the merge
    # this ruling replaced. Pinned per file: the row is duplicated, so a fix
    # applied to one and not the other is exactly the D-051 half-fix.
    (
        "report-seal-plugin-readme",
        "GI-006",
        PLUGIN_README,
        "may append prose under a heading of their own, which the F6 seal "
        "carries verbatim",
    ),
    (
        "report-seal-root-readme",
        "GI-006",
        ROOT_README,
        "may append prose under a heading of their own, which the F6 seal "
        "carries verbatim",
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
    #
    # D-159 widened this from two crossings to three. GI-002 names the boundary
    # "before ASSAY/NYQUIST/DONE", and the crossing INTO F5.5 is the one the
    # word NYQUIST names -- documented as a two-door rule, it was the door that
    # kept the pre-D-149 evaluation while both others took the three-state one.
    # Driven at cycle 9: a run at F5 with one committed log that no longer
    # reproduced refused `Foundry-Phase('nyquist')` before the mandated strip
    # and PASSED the identical call after it, `corpus_size: 0`, while
    # `nyquist_done` and `done` on that same stripped tree both refused naming
    # EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP.
    (
        "evidence-three-terminal-crossings-not-two",
        "GI-002",
        START_MD,
        "**Three terminal crossings take that same three-state evaluation, "
        "not two**",
    ),
    (
        "evidence-terminal-crossings-named",
        "GI-002",
        START_MD,
        '`Foundry-Phase("nyquist")` INTO F5.5, '
        '`Foundry-Phase("nyquist_done")` out of F5.5 and '
        '`Foundry-Phase("done")` at F6 sweep the same corpus by the same rule',
    ),
    (
        "evidence-entry-crossing-is-not-an-exception",
        "CT-007",
        START_MD,
        "GI-002 names the boundary before NYQUIST alongside the two after it",
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
    # AC-016 / US-004 (D-169): the lead-in above says both doors check the
    # WIDTH, and the sentence that followed it named a RULE -- "ASSAY is only
    # opened by an INSPECT whose recorded rule is final_gate". The gate's own
    # checklist entry is `inspect_ran_at_full_width` and its `ok` is
    # `mode == "FULL"`; the rule is interpolated into the refusal text and read
    # by nothing. Driven at `Foundry-Gate(phase='assay')` on runs recorded
    # FULL/first_of_phase, FULL/final_gate and FULL/verifier_touched: all three
    # returned ok True. So the paragraph contradicted itself, and the half a
    # lead acts on was the false half.
    (
        "assay-doors-test-width-never-the-rule",
        "AC-016",
        START_MD,
        "**WIDTH is the whole condition. The `rule` printed beside it is a "
        "label on HOW that width was reached, and neither door tests it.**",
    ),
    (
        "every-full-rule-opens-assay",
        "AC-016",
        START_MD,
        "Every member of `INSPECT_FULL_RULES` opens ASSAY — `first_of_phase`, "
        "`final_gate` and `verifier_touched` alike",
    ),
    # US-004: a verifier-touching GRIND is the ordinary cycle on a run that
    # builds the verifier, and the retired sentence charged every one of them a
    # widening cycle it does not owe -- exactly the ceremony US-004 removes.
    (
        "verifier-touched-owes-no-widening-cycle",
        "US-004",
        START_MD,
        "owes no extra widening cycle",
    ),
    # AC-016: the lead has to recognise the gate's refusal by the name the
    # checklist prints, which is the predicate rather than the prose beside it.
    (
        "assay-gate-names-its-width-check",
        "AC-016",
        START_MD,
        "a checklist entry literally named `inspect_ran_at_full_width`",
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
        "**`Foundry-Context` READS the run back on the far side of a handover; it saves nothing**",
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
    # --- D16: the F0 Holmes review (fallout AC-016 / FR-031 / FR-056 / OT-017)
    # The command and the report path are both LITERAL: the RESEARCH_AUDIT
    # stream reads `research/*.md`, so a review saved anywhere else is a review
    # no stream ever opens, and a step that names no path leaves where to save
    # it to the lead's judgement at the one moment judgement is not wanted.
    (
        "holmes-step-names-the-command-and-the-path",
        "FR-031",
        START_MD,
        "run `/holmes:review` on the orchestrator and save the report as "
        "`foundry-archive/{run}/research/holmes-orchestrator.md`",
    ),
    (
        "holmes-step-names-the-roster",
        "AC-016",
        START_MD,
        "Saving it there is what puts it on the RESEARCH_AUDIT roster",
    ),
    # Both conditions, stated as both: a self-targeting run that moves no
    # module has no boundary to judge, and a review of nothing on the roster is
    # a document RESEARCH_AUDIT then holds the build against.
    (
        "holmes-step-is-conditional-on-two-facts",
        "FR-056",
        START_MD,
        "**Both conditions, never one.**",
    ),
    # --- D17: F0.5 groups by behaviour and records ownership -----------------
    # fallout AC-043 / FR-009 / FR-052. Holmes flow-7's skeptic section records
    # that there is no decompose tool -- `castings/manifest.json` is authored
    # by the agents F0.5 spawns -- so this instruction is the ONLY place
    # `requirement_ids` can be created. F0.9 can validate the span afterwards;
    # it cannot invent the ownership the manifest never recorded.
    (
        "f05-groups-by-behaviour",
        "AC-043",
        START_MD,
        "Group by BEHAVIOUR, and record what each casting owns",
    ),
    (
        "f05-names-the-five-surfaces-of-one-behaviour",
        "AC-043",
        START_MD,
        "the door itself, its report row, its display line, its command prose "
        "and its README row",
    ),
    (
        "f05-layer-split-is-the-exception",
        "AC-043",
        START_MD,
        "only where those surfaces genuinely cannot share an owner",
    ),
    (
        "f05-writes-requirement-ids",
        "FR-009",
        START_MD,
        "the requirement ids that casting owns, persisted beside its `key_files`",
    ),
    (
        "f05-records-split-reason-above-two",
        "FR-052",
        START_MD,
        "naming every id that lands on MORE THAN TWO castings",
    ),
    (
        "f09-refuses-an-unrecorded-span",
        "FR-052",
        START_MD,
        "**F0.9 REFUSES a span above two without one**",
    ),
    # --- D18: the two flags that were parsed and read by nothing -------------
    # fallout FR-044 / GI-015 (temper) and AC-052 / FR-033 / FR-055 (no_ui).
    # GI-015 is a global invariant rather than a preference: a paragraph that
    # defaults `--temper` on, or that reads as though a TEMPER-off run does no
    # adversarial verification, violates it and GI-005 together.
    (
        "temper-threading",
        "FR-044",
        START_MD,
        "thread the `--temper` invocation flag through by passing "
        "`temper=<FOUNDRY_TEMPER>`",
    ),
    ("temper-defaults-off", "GI-015", START_MD, "**`--temper` DEFAULTS OFF.**"),
    (
        "temper-off-keeps-prove-adversarial",
        "GI-015",
        START_MD,
        "On a TEMPER-off run PROVE keeps its adversarial half AT INSPECT",
    ),
    (
        "temper-decides-the-phase-not-the-existence",
        "GI-015",
        START_MD,
        "The adversarial work happens on every run; the flag decides WHICH "
        "PHASE does it",
    ),
    (
        "no-ui-threading",
        "FR-055",
        START_MD,
        "thread the `--no-ui` invocation flag through by passing "
        "`no_ui=<FOUNDRY_NO_UI>`",
    ),
    # --- D19: the halt door and where the run is heading (fallout FR-047) ----
    (
        "halt-door-is-the-same-door-the-cap-reaches",
        "FR-047",
        START_MD,
        "`Foundry-Phase(phase='halt', reason=…, text=…)` is the door",
    ),
    (
        "halt-door-refuses-exactly-three-things",
        "FR-047",
        START_MD,
        "a reason that is not a member, a team still registered, and a run "
        "that is already `HALTED`",
    ),
    (
        "halt-is-terminal",
        "FR-047",
        START_MD,
        "**`HALTED` is terminal and there is no second halt:**",
    ),
    (
        "every-next-names-the-ending",
        "FR-047",
        START_MD,
        "**Every `Foundry-Next` response names where the run is heading**",
    ),
    # `null` and `0` are opposite facts and a reader that conflates them halts
    # a run that had no cap at all -- `_terminal_outlook`'s own docstring says
    # so, and the prose has to carry it or the field is a trap.
    (
        "cycles-to-cap-null-is-not-zero",
        "FR-047",
        START_MD,
        "**`cycles_to_cap` is `null` on an unbounded run, and `null` and `0` "
        "are opposite facts:**",
    ),
    # --- D20: the agent records, the lead confirms (fallout OT-029 / FR-049) -
    (
        "lead-does-not-record-a-stream",
        "OT-029",
        START_MD,
        "**YOU DO NOT RECORD A STREAM. THE AGENT DOES.**",
    ),
    (
        "lead-confirms-the-record-exists",
        "OT-029",
        START_MD,
        "CONFIRM ITS RECORD EXISTS",
    ),
    (
        "stream-record-replaces-rather-than-sums",
        "FR-049",
        START_MD,
        "a second record for the same `(stream, cycle)` REPLACES the first",
    ),
    (
        "a-missing-record-is-a-finding-not-a-gap",
        "FR-049",
        START_MD,
        "**If a stream finished and no record exists, that is a finding about "
        "the stream**",
    ),
    # --- D21: the F0.7 call the schema accepts (fallout AC-051 / FR-032) -----
    (
        "f07-is-not-a-gated-transition",
        "AC-051",
        START_MD,
        "**F0.7 is not a gated phase transition, and `intent_coverage` is not "
        "a gate token.**",
    ),
    # --- D22: --max-cycles on a resume (fallout FR-020 / FR-047) -------------
    # `setup-foundry.sh` exits on the `resume` subcommand BEFORE its argument
    # loop and resume.md never runs it, so there is no echoed value to copy
    # here. A paragraph that told the lead to read one would send it looking
    # for a line that is never printed.
    (
        "resume-takes-n-from-the-invocation",
        "FR-020",
        RESUME_MD,
        "**Take N from the invocation itself.**",
    ),
    (
        "resume-rewrites-the-persisted-cap",
        "FR-020",
        RESUME_MD,
        "REWRITES `state.json.max_cycles`",
    ),
    # fallout FR-020 / CT-006 (D-067): this row read "`0` is unbounded, and the
    # default", which was true of the wire and false of the door -- and the
    # sentence beside it, "Omitting the flag changes nothing ... Only a positive
    # N rewrites it", was the prose half of the bug. `max_cycles` now defaults to
    # `None` at the handler, so absence, 0 and N are three answers; all three are
    # pinned, because a pin on only one of them cannot see the other two merge.
    (
        "resume-omitted-flag-leaves-the-cap-alone",
        "FR-020",
        RESUME_MD,
        "Omitting `--max-cycles` leaves the run's persisted cap exactly where it was",
    ),
    (
        "resume-explicit-zero-lifts-the-ceiling",
        "FR-020",
        RESUME_MD,
        "`--max-cycles 0` REWRITES the cap to unbounded",
    ),
    (
        "resume-positive-n-rewrites-the-cap",
        "FR-020",
        RESUME_MD,
        "Any positive N rewrites the cap to N.",
    ),
    (
        "resume-below-the-cycle-halts-at-the-grind-door",
        "FR-047",
        RESUME_MD,
        "**A value BELOW the cycle the run is on halts it at the next GRIND "
        "door**, with reason `cap_reached`",
    ),
    (
        "resume-halt-is-a-transition-not-a-refusal",
        "FR-047",
        RESUME_MD,
        "That is a SUCCESSFUL `Foundry-Phase` transition and never a refusal",
    ),
    # --- D23: the rationale sections (fallout OT-027 / FR-047 / FR-036) ------
    (
        "named-backlog-is-a-successful-end",
        "OT-027",
        LEAD_DISCIPLINE,
        "**A run that reaches `HALTED` with every open finding written down "
        "and tiered has succeeded.**",
    ),
    (
        "empty-prove-is-not-the-goal",
        "FR-047",
        LEAD_DISCIPLINE,
        "an empty PROVE is not the goal",
    ),
    # FR-036 is Locked as a RECORD: "Nothing beyond FULL width; record it as a
    # documented residual risk." The deliverable is the written record, so the
    # pin is on the sentence that refuses to promise a mitigation.
    (
        "self-hosting-residual-risk-is-recorded",
        "FR-036",
        LEAD_DISCIPLINE,
        "**Nothing mitigates this beyond `FULL` width on `verifier_touched`, "
        "and that is an accepted residual risk of self-hosting rather than a "
        "gap someone is going to close later.**",
    ),
    # --- D24: the setup script threads what it echoes (fallout FR-055) ------
    # The echo block and the threading sentence disagreed: FOUNDRY_TEMPER and
    # FOUNDRY_NO_UI were printed and then not named as things to thread, which
    # is how a flag ends up parsed, echoed and read by nothing.
    (
        "script-threads-every-echoed-flag",
        "FR-055",
        SETUP_SH,
        "url=$URL, temper=$TEMPER, nyquist=$NYQUIST, max_cycles=$MAX_CYCLES, "
        "no_ui=$NO_UI",
    ),
    (
        "script-names-every-echoed-value",
        "FR-055",
        SETUP_SH,
        "the FOUNDRY_URL, FOUNDRY_TEMPER, FOUNDRY_NYQUIST, FOUNDRY_MAX_CYCLES "
        "and FOUNDRY_NO_UI values above",
    ),
    ("script-echoes-no-ui", "AC-052", SETUP_SH, "FOUNDRY_NO_UI=$NO_UI"),
    (
        "script-usage-names-the-resume-flag",
        "FR-020",
        SETUP_SH,
        "/foundry:resume [--max-cycles N]",
    ),
    # --- D25: the ordering token belongs to the LEAD (fallout FR-055) ------
    # D-089: `foundry_get_context` reached `foundry_next_action` with no
    # caller, so it took the lead value and a SUB-AGENT's orienting
    # Foundry-Context armed `.next-action-called` -- the sole precondition of
    # `foundry_gate` and `foundry_mark_phase_complete`. Casting 2 carries the
    # caller scope into that door; these pin the half the LEAD reads, on both
    # doors into the loop, because a lead that believes a sub-agent's read
    # satisfied the handshake gates on a consultation it never made.
    (
        "gate-ordering-token-is-the-leads",
        "FR-055",
        START_MD,
        "**The ordering token is YOURS, and no sub-agent can arm it for you.**",
    ),
    (
        "caller-argument-scopes-both-guidance-doors",
        "FR-055",
        START_MD,
        "**`Foundry-Next` and `Foundry-Context` both carry a `caller` "
        "argument, and it exists for exactly this.**",
    ),
    (
        "subagent-call-never-counts-as-the-leads",
        "FR-055",
        START_MD,
        "never count a sub-agent's call as having made it for you",
    ),
    (
        "handover-context-is-still-caller-scoped",
        "FR-055",
        START_MD,
        "What it is NOT free of is caller scope",
    ),
    (
        "resume-does-not-fold-context-into-next",
        "FR-055",
        RESUME_MD,
        "**Do not fold steps 2 and 3 into one.**",
    ),
    # --- D26: an empty verdicts ledger is ASSAY NOT RUN (fallout GI-001) ---
    # D-070: the F4 branch of `_compute_next_action` computed `non_verified`
    # and `total` from an EMPTY ledger and fell through to the auto-pass tail,
    # so on entering F4 the lead was told "ASSAY passed: all requirements
    # verified" and to transition onward -- with zero assayers spawned and
    # `verdicts.json` never written. The lead protocol is "follow Foundry-Next
    # literally", so guidance instructed the lead to skip the phase. Casting 2
    # branches on the zero; this is the sentence that makes the lead's own
    # reading of a zero count unambiguous, and it is a COUNT rather than a
    # judgement so it costs the lead no deliberation.
    (
        "empty-verdict-ledger-is-not-an-assay-pass",
        "GI-001",
        START_MD,
        '**Zero recorded verdicts is the state F4 OPENS in, and it is never '
        '"ASSAY passed".**',
    ),
    (
        "never-leave-f4-on-an-unwatched-count",
        "GI-001",
        START_MD,
        "**Never transition out of F4 on a count you did not watch go up.**",
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
    # D-159 / GI-002: the two-door count. GI-002 names three boundaries and the
    # crossing INTO F5.5 is one of them, so a sentence that closed the subject
    # at "both terminal doors" told a --nyquist lead the entry crossing was
    # already covered by a rule it did not yet take.
    (
        "Both terminal doors take that same evaluation",
        "GI-002",
        "three terminal crossings take it: `Foundry-Phase(\"nyquist\")` into "
        "F5.5, `Foundry-Phase(\"nyquist_done\")` out of it, and "
        "`Foundry-Phase(\"done\")` at F6",
    ),
    # D-169 / AC-016: the condition NO door evaluates. start.md copied the
    # inspect_clean refusal verbatim, and the refusal named a rule while the
    # check beside it -- `inspect_ran_at_full_width`, `ok` = `mode == "FULL"`
    # -- named a width. Driven at `Foundry-Gate(phase='assay')` on recorded
    # widths FULL/first_of_phase, FULL/final_gate and FULL/verifier_touched:
    # ok True on all three, so a FULL/verifier_touched INSPECT does open ASSAY
    # and the sentence was false about the ordinary cycle of this very run.
    (
        "ASSAY is only opened by an INSPECT whose recorded rule is final_gate",
        "AC-016",
        "both doors test the recorded WIDTH: every member of "
        "INSPECT_FULL_RULES opens ASSAY, and the rule beside the mode is a "
        "label on how FULL was reached rather than a second test",
    ),
    # fallout AC-051 / FR-032: the call the SCHEMA rejects. `Foundry-Gate`'s
    # enum is `sorted(GATE_TO_TRANSITION)` and F0.7 guards no transition, so it
    # has no token -- the call was refused at the transport, before any handler
    # or checklist could say why, and the step it was meant to open never
    # opened. This is the only retired spelling here whose replacement is a
    # DIFFERENT NUMBER of calls rather than different words, so a positive pin
    # on the new sentence cannot see the old one surviving beside it.
    # fallout FR-055 (D-089): the half of that sentence the driven evidence
    # disproved. `foundry_get_context` calls `foundry_next_action`, and that
    # call WROTE `.next-action-called` -- this run's own artefacts carry the
    # asymmetric signature, `.next-action-called` 19 seconds later than
    # `.last-next-at`, written by a PROVE sub-agent's Foundry-Context. What the
    # handover paragraph was ever claiming is that the call preserves no WORK,
    # which is the half that survives.
    (
        "it writes nothing and saves nothing",
        "FR-055",
        "Foundry-Context saves nothing -- there is no work for it to preserve "
        "before a handover -- and it is scoped by the same `caller` argument "
        "Foundry-Next carries",
    ),
    (
        "Foundry-Gate(phase='intent_coverage')",
        "AC-051",
        "F0.7 is not a gated phase transition: run the check with "
        "Foundry-Intent-Coverage, then Foundry-Gate(phase='validate') to "
        "cross into F0.9",
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


_ASSAY_DOOR_LEAD_IN = (
    "**Both doors into ASSAY check the width by name, and both refuse a "
    "`DELTA` cycle:**"
)


def _assay_door_paragraph() -> str:
    """start.md's ASSAY-door paragraph, flattened.

    Scoped to the ONE paragraph rather than to ``### F2: INSPECT``: the
    width-decision paragraph higher in that section already names all three
    FULL rules while explaining how a mode gets decided, so a section-wide
    search would be satisfied by that paragraph alone and would see nothing at
    all about which recorded widths open the door.
    """
    text = _read(START_MD)
    start = text.find(_ASSAY_DOOR_LEAD_IN)
    assert start != -1, (
        f"{_rel(START_MD)} no longer carries the ASSAY-door lead-in "
        f"{_ASSAY_DOOR_LEAD_IN!r}. That sentence is where the crossing rule is "
        f"stated; if it was reworded, retarget this helper -- do not drop the "
        f"assertions built on it."
    )
    end = text.find("\n\n", start)
    return " ".join(text[start : end if end != -1 else len(text)].split())


def test_the_assay_door_paragraph_names_every_full_rule_as_opening_it() -> None:
    """AC-016 / US-004 / D-169: the doors read WIDTH; the prose named a RULE.

    ``foundry_gate``'s assay branch appends a checklist entry named
    ``inspect_ran_at_full_width`` whose ``ok`` is ``mode == "FULL"``, and the
    recorded rule is interpolated into the refusal string and read by nothing.
    start.md nevertheless quoted the refusal verbatim -- "ASSAY is only opened
    by an INSPECT whose recorded rule is final_gate" -- two clauses after its
    own lead-in saying both doors check the width by name. Driven at
    ``Foundry-Gate(phase='assay')`` on synthetic runs whose recorded width was
    FULL/first_of_phase, FULL/final_gate and FULL/verifier_touched: ok True on
    all three. A verifier-touching GRIND is the ordinary cycle of a run that
    edits the verifier, so the false half of the paragraph charged the lead a
    widening cycle the server does not ask for.

    The expected rules are DERIVED from ``INSPECT_FULL_RULES``: a rule added to
    the closed vocabulary without a mention here fails this test rather than
    silently inheriting a claim the paragraph never made about it.
    """
    from foundry_mcp.schemas import vocab

    para = _assay_door_paragraph()
    assert vocab.INSPECT_FULL_RULES, (
        "vocab.INSPECT_FULL_RULES is empty, which would make the check below "
        "vacuous. The FULL rules are a closed vocabulary; an empty one is a "
        "defect in vocab.py, not a licence to skip this assertion."
    )
    missing = sorted(r for r in vocab.INSPECT_FULL_RULES if f"`{r}`" not in para)
    assert not missing, (
        f"{_rel(START_MD)}'s ASSAY-door paragraph does not name {missing} as "
        f"opening ASSAY. Every member of INSPECT_FULL_RULES records mode FULL, "
        f"and FULL is the whole of what both doors check -- a rule the "
        f"paragraph leaves out reads as a width that still owes a widening "
        f"cycle (D-169)."
    )
    assert f"`{vocab.INSPECT_DELTA_RULE}`" not in para or "DELTA" in para, (
        f"{_rel(START_MD)}'s ASSAY-door paragraph names the delta rule without "
        f"naming the DELTA width it refuses on."
    )


def test_the_assay_door_correction_sits_under_the_claim_it_qualifies() -> None:
    """AC-016 / D-169: the POSITION is the guard, and no phrase pin sees it.

    Every phrase this module pins can be present with the width ruling filed in
    some other section, and a lead reads ``### F2: INSPECT`` top to bottom and
    acts on the first crossing rule it meets. The routing sentence states the
    width, the lead-in says both doors check it, and the ruling that WIDTH is
    the whole condition has to arrive after both -- in the same section, not in
    F4 and not in the escalation section, where a lead deciding this crossing
    has already stopped reading.
    """
    text = _read(START_MD)
    start = text.find("\n### F2: INSPECT")
    assert start != -1, (
        f"{_rel(START_MD)} has no `### F2: INSPECT` section. The crossing rule "
        f"lives inside it; if the heading was renamed, retarget this test -- "
        f"do not drop it."
    )
    end = text.find("\n### ", start + 1)
    section = " ".join(text[start : end if end != -1 else len(text)].split())

    routing = section.find("**Zero blocking defects does not by itself open ASSAY")
    lead_in = section.find(_ASSAY_DOOR_LEAD_IN)
    ruling = section.find("**WIDTH is the whole condition.")
    rules = section.find("Every member of `INSPECT_FULL_RULES` opens ASSAY")
    owes = section.find("owes no extra widening cycle")

    for label, found in (
        ("the width-decides-the-crossing routing sentence", routing),
        ("the both-doors-check-the-width lead-in", lead_in),
        ("the ruling that WIDTH is the whole condition", ruling),
        ("the roster of FULL rules that open ASSAY", rules),
        ("the statement that a verifier-touched cycle owes no widening", owes),
    ):
        assert found != -1, (
            f"{_rel(START_MD)}'s `### F2: INSPECT` section is missing {label}. "
            f"All five belong to the same paragraph: the lead decides this "
            f"crossing there and nowhere else (D-169)."
        )

    assert routing < lead_in < ruling < rules < owes, (
        f"{_rel(START_MD)} states the width ruling out of order. A "
        f"qualification a reader meets before the claim it qualifies reads as "
        f"a different rule, and one they meet after they have already crossed "
        f"is not read at all: routing, then the two doors, then WIDTH is the "
        f"whole condition, then which rules satisfy it, then what that costs "
        f"a verifier-touching cycle (nothing)."
    )


def test_the_escalation_section_qualifies_the_clean_arm_where_it_states_it() -> None:
    """ST-001 / D-157: the guard has to sit UNDER the claim it qualifies.

    Every phrase pinned above can be present with the qualification filed in
    some other section, and a lead reads ``## ESCALATION`` top to bottom: the
    exits table says "two consecutive INSPECT cycles", and a reader who stops
    there counts two crossings from wherever the class is standing. The arm
    skips every crossing at or before ``escalated_at_cycle``, so from a fresh
    escalation the real distance is three. Driven on AC-002's own fixture --
    one LATENT instance filed at a finer boundary in cycles 3, 4 and 5, class
    escalated at 5: ``_class_drew_live_in_cycle(defects, key, 5)`` returned
    False, yet the still-escalated hint offered "2 more INSPECT cycle(s)", and
    walking the arm from that state takes three crossings because the one
    closing cycle 5 is discarded by the guard.

    So the POSITION is asserted, in the section that has to carry both: the row
    first, the guard after it, and the whole of it inside ``## ESCALATION``.
    """
    text = _read(START_MD)
    start = text.find("\n## ESCALATION")
    assert start != -1, (
        f"{_rel(START_MD)} has no `## ESCALATION` section. AC-005 requires it "
        f"and this ordering rule lives inside it; if the heading was renamed, "
        f"retarget this test -- do not drop it."
    )
    end = text.find("\n## ", start + 1)
    section = " ".join(text[start : end if end != -1 else len(text)].split())

    row = section.find("| Clean cycles | `clean_cycles` |")
    guard = section.find("The cycle a class escalated ON is not one of the two.")
    walk = section.find("the exit is THREE crossings away, not two")
    assert row != -1, (
        f"{_rel(START_MD)}'s `## ESCALATION` section no longer carries the "
        f"clean-cycles exit row."
    )
    assert guard != -1, (
        f"{_rel(START_MD)}'s `## ESCALATION` section does not state that the "
        f"cycle a class escalated ON is excluded from the two clean cycles. "
        f"Without it the section states ST-001's count and hides ST-001's "
        f"guard, which is how a lead reads a two-crossing distance off an arm "
        f"that needs three (D-157)."
    )
    assert walk != -1, (
        f"{_rel(START_MD)}'s escalation guard paragraph no longer walks the "
        f"crossings. The count is the part a lead acts on: the crossing that "
        f"closes the escalation cycle is skipped, the next banks one, and only "
        f"the third clears."
    )
    assert row < guard < walk, (
        f"{_rel(START_MD)} states the clean-cycle guard before the exit row it "
        f"qualifies. A qualification a reader meets before the claim reads as "
        f"a different rule; it has to sit under the row."
    )


def test_the_f6_evidence_rung_names_every_terminal_crossing_in_order() -> None:
    """GI-002 / D-159: three crossings take this rung, and prose said two.

    GI-002 names the sweep "before ASSAY/NYQUIST/DONE". The crossing the word
    NYQUIST names is ``Foundry-Phase('nyquist')`` -- the F5-to-F5.5 entry --
    and while this paragraph closed the subject at "both terminal doors" that
    entry was the one arm still refusing on the sweep's ``ok`` alone, with no
    reading of ``corpus_size`` or a recorded pre-strip pass. Driven at cycle 9:
    on a run at F5 with one committed log that no longer reproduced, the entry
    call refused naming the log; after the strip ``commands/start.md`` mandates
    verbatim, the identical call returned ok with ``corpus_size: 0`` while
    ``nyquist_done`` and ``done`` on that same tree both refused naming
    EVIDENCE_CORPUS_STRIPPED_BEFORE_SWEEP.

    Asserted as ORDER, because a paragraph can name all three crossings and
    still present the entry as an afterthought to a two-door rule: the count
    comes first, then the crossings in the order a --nyquist run makes them.
    """
    text = _read(START_MD)
    parts = text.split("### F6: DONE", 1)
    assert len(parts) == 2, (
        f"{_rel(START_MD)} has no `### F6: DONE` section; the evidence rung's "
        f"crossings are asserted inside it."
    )
    section = " ".join(parts[1].split("\n## ", 1)[0].split())

    count = section.find(
        "**Three terminal crossings take that same three-state evaluation, "
        "not two**"
    )
    assert count != -1, (
        f"{_rel(START_MD)}'s F6 evidence-lifecycle step no longer states how "
        f"many terminal crossings take the three-state rung. Two of three is "
        f"the shape D-159 filed: the crossing the rung was not applied to is "
        f"the one an operator never thinks to check."
    )
    crossings = (
        '`Foundry-Phase("nyquist")` INTO F5.5',
        '`Foundry-Phase("nyquist_done")` out of F5.5',
        '`Foundry-Phase("done")` at F6',
    )
    at = [section.find(c) for c in crossings]
    missing = [c for c, pos in zip(crossings, at) if pos == -1]
    assert not missing, (
        f"{_rel(START_MD)}'s F6 evidence-lifecycle step does not name "
        f"{missing}. GI-002 names three boundaries; a roster short by one "
        f"documents the missing crossing as covered by a rule it does not take."
    )
    assert count < min(at), (
        f"{_rel(START_MD)} names the terminal crossings before it says how "
        f"many take the rung. The count is what a reader checks the list "
        f"against."
    )
    assert at == sorted(at), (
        f"{_rel(START_MD)} lists the terminal crossings out of run order "
        f"({[c for _, c in sorted(zip(at, crossings))]}). A --nyquist run "
        f"makes them entry, exit, done, and the entry crossing is the one that "
        f"was missing -- listing it last reads as the afterthought it was."
    )


@pytest.mark.parametrize(
    "heading",
    (
        "## Why the lead-fix lane is bounded",
        "## Why dispatch is a pointer",
        # fallout OT-027 / FR-047 and FR-036 -- the two sections this release
        # added. They join the parametrize rather than getting a test of their
        # own, because the property is the FILE's and a section with its own
        # bespoke check is a section free to drift into its own shape.
        "## Why a named backlog is a successful end",
        "## Why a self-hosting run carries a residual risk",
    ),
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


#: Ways a file can promise the seal the cycle-27 ruling REPLACED -- a merge
#: that put the lead's prose back below the section it was typed under. Swept
#: over the whole corpus rather than pinned as a retired start.md spelling,
#: because this claim was in THREE files at once (start.md's F6 step and the
#: `Foundry-Report` row of BOTH READMEs, which are byte-identical to each
#: other) and a per-file absence pin is what lets the third copy survive.
#: Matched case-insensitively: start.md shouted its copy and the READMEs did
#: not, so a case-sensitive sweep would have caught one spelling of one claim.
_RETIRED_SEAL_PROMISES = (
    "append prose below any section",
    "append prose below a section",
    "append prose below them",
)


def test_no_owned_prose_promises_the_pre_ruling_seal() -> None:
    """D-228 / D-230 / GI-006: the seal carries, it does not merge.

    The replaced seal told lead prose from generated prose with a line-granular
    ``difflib`` comparison and kept the ``replace`` opcodes as the lead's, so a
    generated row whose value moved between two generations came back as "prose
    you appended". Driven with ZERO prose appended: the HALTED transition
    reported ``lead_prose_lines: 11`` and the sealed document carried a stale
    ``| GRIND cycles | 22 |  | 12 | 2 |`` directly below the fresh row saying
    ``1``. The ruling made the seal coarser instead of better-tuned -- it
    carries what lies OUTSIDE the generated skeleton, verbatim, into one
    trailing ``## Lead notes (carried by the seal)`` section.

    The cost is that prose typed INSIDE a generated section's body is no longer
    carried, which turns "append below a section" from a convenience into an
    instruction that loses the lead's work silently at the next terminal
    transition. So the promise has to be gone from every file that makes it,
    not merely replaced in the one a defect was filed against.
    """
    for path in _LEAD_PROSE_CORPUS:
        flat = _flat(path).lower()
        for promise in _RETIRED_SEAL_PROMISES:
            assert promise not in flat, (
                f"{_rel(path)} still promises {promise!r} (GI-006). The F6 "
                f"seal carries only what lies outside the generated skeleton "
                f"-- a heading the generator did not write, or the lines above "
                f"the first generated section -- into one trailing `## Lead "
                f"notes (carried by the seal)` section. Prose typed below a "
                f"generated heading sits INSIDE that section's body and is "
                f"regenerated away. Say where prose survives, not where it "
                f"used to be merged back to."
            )


# ---------------------------------------------------------------------------
# The flag that was parsed, echoed and read by nothing (fallout AC-057 / FR-059)
# ---------------------------------------------------------------------------
#
# `--output-dir` was accepted by the argument loop, stored in `OUTPUT_DIR`,
# printed twice (`Output: ...` and `FOUNDRY_OUTPUT=...`) and then read by
# nothing at all: no tool took the value, no archive moved because of it, and
# an operator who passed it got a flag that did nothing and confirmed it twice.
# The deliverable is a DELETION, and a deletion is invisible to every positive
# test -- nothing else in this module would notice the flag coming back.
#
# EVERY SURFACE THAT EVER NAMED THE FLAG, not the two whose owner deleted it.
# This roster was narrow for one wave and one reason: both READMEs can only
# move in the same commit as `plugin.json` / `pyproject.toml`, because
# `test_readme_badges_agree_with_the_shipped_versions` and
# `test_readme_describes_the_release_it_badges` pin their badges and release
# headings against those manifests -- so while that commit was outstanding a
# sweep covering the READMEs would have been RED for a reason nobody could
# then fix. That commit has landed, and the narrow roster is what let the flag
# survive in `commands/help.md`: help.md names no casting owner, so a sweep
# scoped to owned surfaces was the one place the deletion could hide
# (D-030 / D-038). A roster scoped to who-owns-what sweeps the files that were
# already fixed and misses the file nobody was watching.
#
# `plugins/forge` has an `--output-dir` of its own, which is a DIFFERENT flag on
# a different plugin and is correctly untouched. If the repo-root `README.md`
# ever documents forge's flag surface, SCOPE this sweep to the foundry section
# rather than dropping the path -- the root README is where a reader learns
# what `/foundry:start` accepts.
_OUTPUT_DIR_FREE: tuple[Path, ...] = (
    START_MD,
    RESUME_MD,
    HELP_MD,
    SETUP_SH,
    PLUGIN_README,
    ROOT_README,
)

#: Every spelling the flag reached a surface under. Three, because deleting the
#: user-facing `--output-dir` while leaving `OUTPUT_DIR="$2"` in the argument
#: loop is a flag that is still parsed and merely undocumented, which is worse
#: than the state this replaced.
_OUTPUT_DIR_SPELLINGS = ("--output-dir", "OUTPUT_DIR", "FOUNDRY_OUTPUT")


@pytest.mark.parametrize("path", _OUTPUT_DIR_FREE, ids=_rel)
def test_the_output_dir_flag_is_gone_from_every_lead_facing_surface(
    path: Path,
) -> None:
    """fallout AC-057 / FR-059: deleted from every surface, not just documented away."""
    text = _read(path)
    # Floor first: an absence assertion over an empty string forbids nothing,
    # and both of these files are load-bearing enough that "it read as empty"
    # would otherwise pass silently.
    assert len(text) > 1000, (
        f"{_rel(path)} read as {len(text)} characters. An absence assertion "
        f"over a file this short is vacuous -- check the path constant before "
        f"trusting the result below."
    )
    for spelling in _OUTPUT_DIR_SPELLINGS:
        assert spelling not in text, (
            f"{_rel(path)} contains {spelling!r} again (AC-057 / FR-059). The "
            f"flag was parsed, echoed and read by NOTHING; deleting it is the "
            f"requirement. Archives stay under `foundry-archive/`."
        )


def test_the_output_dir_sweep_still_has_something_to_sweep() -> None:
    """Floor: all six paths exist and the roster did not silently narrow.

    A tuple that silently emptied -- a renamed constant, a moved file -- would
    parametrize zero cases and report success while forbidding nothing. The
    count is pinned rather than merely non-zero because the failure this sweep
    was filed against (D-030) was a roster that covered five of the six
    surfaces and reported success on the sixth by never looking at it.
    """
    assert len(_OUTPUT_DIR_FREE) == 6, _OUTPUT_DIR_FREE
    assert len(set(_OUTPUT_DIR_FREE)) == 6, _OUTPUT_DIR_FREE
    for path in _OUTPUT_DIR_FREE:
        assert path.is_file(), f"{_rel(path)} does not exist"
    # help.md names no casting owner in `castings/manifest.json`, which is why
    # it kept the flag through the deletion. If it ever leaves this roster, the
    # same blind spot reopens.
    assert HELP_MD in _OUTPUT_DIR_FREE, (
        f"{_rel(HELP_MD)} left the `--output-dir` sweep. It has no casting "
        f"owner, so no owner-scoped sweep covers it -- this roster is the only "
        f"thing that does (D-030 / D-038)."
    )


# ---------------------------------------------------------------------------
# Vocabularies the prose states and the server enforces
# ---------------------------------------------------------------------------
#
# Each check below derives its expected set from the CODE constant rather than
# re-typing it here, in the `_PYTEST_DISCOVERY_PHRASE` shape: a vocabulary that
# changes then fails on both sides at once instead of leaving the protocol
# advertising a set the server no longer holds.


def _section(path: Path, heading: str, level: str = "### ") -> str:
    """The named section's text, heading to the next heading of that level."""
    text = _read(path)
    start = text.find(heading)
    assert start != -1, f"{_rel(path)} has no {heading!r} section."
    end = text.find("\n" + level, start + 1)
    return text[start : end if end != -1 else len(text)]


def test_start_md_names_every_halt_reason_the_door_accepts() -> None:
    """fallout FR-047 / CT-004 / CT-005: the four members, derived from vocab.

    ``_halt_preconditions`` refuses a reason outside ``HALT_REASONS`` and
    prints the accepted set from the constant. A lead reading start.md has to
    be able to pick a member BEFORE the refusal, so the protocol carries the
    same four -- derived here, so a fifth member added to the vocabulary fails
    this instead of shipping a door the protocol cannot name a reason for.
    """
    from foundry_mcp.schemas import vocab

    assert len(vocab.HALT_REASONS) >= 4, sorted(vocab.HALT_REASONS)
    flat = _flat(START_MD)
    missing = [r for r in sorted(vocab.HALT_REASONS) if f"`{r}`" not in flat]
    assert not missing, (
        f"{_rel(START_MD)} does not name the halt reason(s) {missing}. Every "
        f"member of HALT_REASONS is a value the lead may have to pass at "
        f"`Foundry-Phase(phase='halt')`; a member the protocol never mentions "
        f"is one the lead discovers from a refusal."
    )


def test_the_f07_step_calls_a_gate_token_the_schema_accepts() -> None:
    """fallout AC-051 / FR-032: the F0.7 step named a token the enum rejects.

    ``Foundry-Gate``'s schema is ``{"enum": sorted(GATE_TO_TRANSITION)}``, so a
    call naming ``intent_coverage`` is rejected at the MCP boundary before any
    handler runs -- not refused with a checklist, REJECTED, which is why the
    step it was supposed to open simply never opened and nothing said why.

    Derived from the mapping table rather than pinned to the replacement token:
    hard-coding the token here would go green the day someone updated the test
    instead of the prose, which is the exact failure this requirement exists to
    close.
    """
    from foundry_mcp.tools.orchestration.gates import GATE_TO_TRANSITION

    # Floor: an empty or tiny mapping makes every membership test below pass.
    assert len(GATE_TO_TRANSITION) >= 10, sorted(GATE_TO_TRANSITION)
    assert "intent_coverage" not in GATE_TO_TRANSITION, (
        "`intent_coverage` is now a gate token. If a transition was really "
        "added for F0.7, rewrite the F0.7 step to call it -- do not delete "
        "this assertion, rewrite the prose it guards."
    )

    section = _section(START_MD, "### F0.7: INTENT-CARRIER")
    called = re.findall(r"Foundry-Gate\(phase='([a-z_]+)'\)", section)
    assert called, (
        f"{_rel(START_MD)}'s F0.7 step names no `Foundry-Gate(phase='...')` "
        f"call at all. The step still has to cross into F0.9, and the token it "
        f"crosses with is the thing this checks."
    )
    invalid = sorted({t for t in called if t not in GATE_TO_TRANSITION})
    assert not invalid, (
        f"{_rel(START_MD)}'s F0.7 step calls Foundry-Gate with {invalid}, "
        f"which the schema enum rejects. Accepted tokens are "
        f"{sorted(GATE_TO_TRANSITION)}."
    )


def test_the_f09_step_gates_the_transition_that_actually_opens_cast() -> None:
    """fallout AC-059 / GI-031: membership is not the property; the MAPPING is.

    D-042 moved the `cast` gate token off `start_cast` and onto the same-name
    `cast` transition, which is what `GATE_TO_TRANSITION` says today. The F0.9
    step went on calling `Foundry-Gate(phase='cast')` -- and that token is
    still a perfectly valid enum member, so the F0.7 check above, which tests
    only membership, stayed GREEN while the lead was being handed the
    F1-COMPLETE checklist (streams, sight, defects) instead of the manifest,
    oversize and file-overlap rungs the CAST wave depends on. A lead following
    the step literally reads the wrong checklist and very likely a refusal.

    Derived from the mapping table rather than pinned to the replacement
    token, in the `_PYTEST_DISCOVERY_PHRASE` shape: move `start_cast` to a
    different gate again and this fails beside the prose instead of one cycle
    after it.
    """
    from foundry_mcp.tools.orchestration.gates import GATE_TO_TRANSITION

    gates_for_start_cast = sorted(
        gate for gate, transitions in GATE_TO_TRANSITION.items()
        if "start_cast" in transitions
    )
    # Floor: with nothing mapping to `start_cast` the membership test below is
    # vacuous -- every token would satisfy an expectation of nothing.
    assert gates_for_start_cast, (
        "No gate token maps to `start_cast` in GATE_TO_TRANSITION, so the "
        "transition that opens CAST has no gate at all. That is a GI-031 "
        "violation in the table; fix the table, not this assertion."
    )

    section = _section(START_MD, "### F0.9: VALIDATE")
    called = re.findall(r"Foundry-Gate\(phase='([a-z_]+)'\)", section)
    assert called, (
        f"{_rel(START_MD)}'s F0.9 step names no `Foundry-Gate(phase='...')` "
        f"call at all. F0.9 closes into F1 CAST, and the token it closes with "
        f"is the thing this checks."
    )
    wrong = sorted(
        token for token in called
        if "start_cast" not in GATE_TO_TRANSITION.get(token, ())
    )
    assert not wrong, (
        f"{_rel(START_MD)}'s F0.9 step calls Foundry-Gate with {wrong}, which "
        f"maps to {[GATE_TO_TRANSITION.get(t) for t in wrong]} rather than to "
        f"`start_cast` -- the transition that opens CAST. The gate token(s) "
        f"that evaluate `_start_cast_preconditions` are {gates_for_start_cast}."
    )


def test_the_f3_close_gates_the_transition_that_reopens_inspect() -> None:
    """fallout D-059 / GI-001 / AC-059: the GRIND -> INSPECT crossing had no gate.

    GI-001's violation column reads "a casting that ... lets a transition skip
    its gate". The F3 close read "commit -> `Foundry-Phase(phase='inspect_start')`
    -> back to F2 INSPECT" and named no gate at all, so the one token AC-059
    added to `GATE_TO_TRANSITION` for that transition was named by NO
    lead-facing surface: `guidance.py`'s `transition_to_inspect` imperative
    named `inspect` (D-058) and this file named nothing.

    Driven at the real door: a lead following the guidance called
    `Foundry-Gate(phase='inspect')` from F3 and was refused, because `inspect`
    maps to the `cast` transition -- the F1 -> F2 crossing, whose preconditions
    are a different question asked two phases earlier. The refusal's own hint
    had to supply `inspect_start`. Both tokens are valid enum members, so the
    schema rejects neither and the mistake is invisible until the door.

    The expected token is DERIVED from the mapping table, in the
    `_PYTEST_DISCOVERY_PHRASE` shape and for the reason
    `test_the_f09_step_gates_the_transition_that_actually_opens_cast` states:
    a literal pin would go green the day someone swapped the prose for another
    valid-but-wrong member, which is exactly the failure being closed here.
    """
    from foundry_mcp.tools.orchestration.gates import GATE_TO_TRANSITION

    gates_for_inspect_start = sorted(
        gate for gate, transitions in GATE_TO_TRANSITION.items()
        if "inspect_start" in transitions
    )
    # Floor: with nothing mapping to `inspect_start` every membership test
    # below is vacuous -- any token would satisfy an expectation of nothing.
    assert gates_for_inspect_start, (
        "No gate token maps to `inspect_start` in GATE_TO_TRANSITION, so the "
        "transition that closes GRIND back into INSPECT has no gate at all. "
        "That is the GI-001 violation in the table itself; fix the table, not "
        "this assertion."
    )

    section = _section(START_MD, "### F3: GRIND")
    gate_calls = list(re.finditer(r"Foundry-Gate\(phase='([a-z_]+)'\)", section))
    assert gate_calls, (
        f"{_rel(START_MD)}'s F3 section names no `Foundry-Gate(phase='...')` "
        f"call at all. F3 closes back into F2 INSPECT, and the gate on that "
        f"crossing is the thing this checks. The gate token(s) that evaluate "
        f"`_inspect_start_preconditions` are {gates_for_inspect_start}."
    )

    # The FIRST call, not any call. The section is read top-down and its close
    # is the crossing instruction, so the first token it hands the lead is the
    # one the lead calls. Checking every occurrence instead would forbid the
    # section from ever naming a token NOT to use -- and naming `inspect` as
    # the trap it is is precisely how a reader is kept off it. `inspect` stays
    # correct at F1 step 7, where it gates the `cast` transition; inside F3
    # there is no `cast` transition for it to guard.
    first_call = gate_calls[0]
    first_token = first_call.group(1)
    assert "inspect_start" in GATE_TO_TRANSITION.get(first_token, ()), (
        f"{_rel(START_MD)}'s F3 section hands the lead "
        f"`Foundry-Gate(phase='{first_token}')` first, which maps to "
        f"{GATE_TO_TRANSITION.get(first_token)} rather than to `inspect_start` "
        f"-- the transition that closes GRIND. `inspect` in particular maps to "
        f"`cast`, the crossing INTO INSPECT from F1: called from F3 it is "
        f"refused naming a phase the lead already left, which is how the "
        f"missing gate stayed invisible for a release. The gate token(s) that "
        f"evaluate `_inspect_start_preconditions` are {gates_for_inspect_start}."
    )

    # A gate named AFTER the transition it guards is not a gate. The class this
    # closes is literally "lead-surface-omits-the-gate-before-the-transition",
    # so the ORDER is half the property.
    first_transition = section.find("Foundry-Phase(phase='inspect_start')")
    assert first_transition != -1, (
        f"{_rel(START_MD)}'s F3 section no longer names "
        f"`Foundry-Phase(phase='inspect_start')`. That call is the boundary "
        f"crossing that advances the cycle counter; the section cannot close "
        f"without it."
    )
    assert first_call.start() < first_transition, (
        f"{_rel(START_MD)}'s F3 section names its gate AFTER "
        f"`Foundry-Phase(phase='inspect_start')`. **Gate then Phase** is the "
        f"protocol's own ordering, and a gate read after the transition "
        f"reports the preconditions of a crossing that already happened."
    )


def test_resume_md_states_the_cap_doors_three_answers() -> None:
    """fallout FR-020 / CT-006: absence, `0` and N are three answers, not two.

    ``foundry_init`` took ``max_cycles: int = 0``, so the resume branch's
    ``if max_cycles:`` could not tell "no flag was passed" from "a cap of 0 was
    passed" -- and 0 is the documented spelling of UNBOUNDED, so the one value
    that means "lift the ceiling" was the one value the door declined to write.
    An operator who typed ``--max-cycles 0`` was told, accurately and
    uselessly, that the cap was still 5. The handler now defaults to ``None``
    and ``server.py`` forwards ``args.get("max_cycles")`` under a schema
    carrying no default, which is what makes the absence a value of its own.

    resume.md documented the bug in as many words: "`0` is unbounded, and the
    default. **Omitting the flag changes nothing** ... Only a positive N
    rewrites it." Both halves are asserted -- the three arms positively in
    ``_PINS``, the retired reading as an absence here, because a positive pin
    cannot see a retired sentence that survives BESIDE its replacement.

    The default is DERIVED from the signature rather than described here, in
    the ``_PYTEST_DISCOVERY_PHRASE`` shape: revert the handler to ``= 0`` and
    this fails beside the prose that documents it, rather than one release
    later when an operator lifts a ceiling and is told it did not move.
    """
    import inspect as _inspect

    from foundry_mcp.tools.foundry import foundry_init

    default = _inspect.signature(foundry_init).parameters["max_cycles"].default
    assert default is None, (
        f"foundry_init's `max_cycles` default is {default!r}, not None. With a "
        f"default of 0 the resume branch cannot distinguish an omitted flag "
        f"from an explicit `--max-cycles 0`, and {_rel(RESUME_MD)}'s "
        f"three-answer paragraph then documents a door that does not behave "
        f"that way. Fix the handler, or rewrite the prose -- do not delete "
        f"this assertion."
    )

    flat = _flat(RESUME_MD)
    retired = [
        spelling
        for spelling in (
            "`0` is unbounded, and the default",
            "**Omitting the flag changes nothing**",
            "Only a positive N rewrites it",
        )
        if spelling in flat
    ]
    assert not retired, (
        f"{_rel(RESUME_MD)} still carries {retired}, which describes the door "
        f"BEFORE the omitted flag and an explicit 0 became different answers. A "
        f"positive N is no longer the only value that rewrites the cap, and an "
        f"omitted flag is no longer a cap of zero."
    )


def test_the_gate_token_set_the_protocol_describes_is_the_mapping_table() -> None:
    """fallout GI-031 / CT-020: the protocol points at the table, not at a copy.

    A hand-typed list of gate tokens in start.md would be a second closed
    vocabulary free to drift from the first. The tools table names
    ``GATE_TO_TRANSITION`` instead, which is the derivation this suite can
    check without the protocol carrying a copy at all.
    """
    flat = _flat(START_MD)
    assert "`GATE_TO_TRANSITION`" in flat, (
        f"{_rel(START_MD)} no longer names `GATE_TO_TRANSITION` as the source "
        f"of the gate token set. Without it the protocol either says nothing "
        f"about which tokens exist or grows a second list to drift."
    )
    assert "`halt`" in flat, (
        f"{_rel(START_MD)} never names the `halt` token. It is a full member "
        f"of PHASE_TOKENS with its own gate token, so a protocol that omits "
        f"it documents a door the lead cannot find."
    )


def test_the_no_ui_meaning_is_the_servers_sentence_on_both_owned_surfaces() -> None:
    """fallout AC-052 / FR-033 / FR-055: one meaning, spelled once.

    ``survey/surface.md`` FI-2 found ``--no-ui`` meaning three different things
    at once -- "skip the browser audit", "suppress orchestrator banners", and a
    SIGHT check treating it as a hard block -- and the three disagree about the
    DIRECTION of the effect, not merely its wording. The server now spells the
    meaning once in ``NO_UI_MEANING``; every surface quotes that sentence
    rather than re-wording it, and this derives the expected text from the
    constant so a re-worded constant fails here instead of leaving a fourth
    reading in the protocol.

    Both READMEs state the same sentence, and casting 9 owns them -- their
    halves are checked there, on the tree that carries them.
    """
    from foundry_mcp.tools.foundry import NO_UI_MEANING

    assert NO_UI_MEANING.startswith("`--no-ui` "), NO_UI_MEANING
    tail = NO_UI_MEANING.split("` ", 1)[1]

    assert NO_UI_MEANING in _flat(START_MD), (
        f"{_rel(START_MD)} does not carry NO_UI_MEANING verbatim: "
        f"{NO_UI_MEANING!r}. The protocol paragraph that threads `no_ui=` must "
        f"quote the server's sentence, not re-word it -- a re-wording is how "
        f"the flag came to mean three things."
    )
    assert tail in _flat(SETUP_SH), (
        f"{_rel(SETUP_SH)} does not carry the meaning {tail!r}. The script is "
        f"where an operator reads the flag before ever seeing the protocol; if "
        f"it says something else, the two surfaces disagree at the first door."
    )


# ---------------------------------------------------------------------------
# The lead confirms a stream record; it never writes one (fallout OT-029)
# ---------------------------------------------------------------------------
#
# `_ACTION_IMPERATIVES["run_streams"]` told the lead to call `Foundry-Stream`
# while four agent files told the agent to call it too, so a cycle could carry
# two accounts of one run -- and the second was numbers the lead never
# measured. GI-016 settles it: the AGENT records, per (stream, cycle), and a
# later record REPLACES the earlier one rather than summing with it.
#
# EVERY LEAD-FACING SURFACE, not the four whose owner rewrote them. This
# roster was narrow for the same wave and the same reason as `_OUTPUT_DIR_FREE`
# above -- `plugins/foundry/README.md` carried the old `Foundry-Stream` row
# ("Mark verification stream complete") and a README can only move in the same
# commit as the manifests its badges are pinned against. That commit has
# landed, so the roster is now every surface a LEAD reads: the three command
# files, the rationale reference, the setup script and both READMEs.
#
# `TEMPER_SKILL` is deliberately absent. A skill file is what the AGENT is
# told, and the agent is exactly who SHOULD be instructed to record -- sweeping
# it here would forbid the correct half of GI-016.
_OWNED_LEAD_SURFACES: tuple[Path, ...] = (
    START_MD,
    RESUME_MD,
    HELP_MD,
    LEAD_DISCIPLINE,
    SETUP_SH,
    PLUGIN_README,
    ROOT_README,
)

#: Spellings that tell the LEAD to record a stream. The first is the exact row
#: start.md carried; the rest are the ways the same instruction re-enters as a
#: rewrite rather than as a copy.
_LEAD_RECORDS_A_STREAM = (
    "Mark verification stream complete",
    "Mark a verification stream complete",
    "record the stream yourself",
    "record each stream",
)


@pytest.mark.parametrize("path", _OWNED_LEAD_SURFACES, ids=_rel)
def test_no_owned_lead_prose_tells_the_lead_to_record_a_stream(path: Path) -> None:
    """fallout OT-029 / FR-049 / GI-016: the agent records; the lead confirms."""
    flat = _flat(path)
    for spelling in _LEAD_RECORDS_A_STREAM:
        assert spelling not in flat, (
            f"{_rel(path)} tells the lead to {spelling!r} (OT-029). The "
            f"verifying AGENT calls `Foundry-Stream` with the counts it "
            f"measured; a lead that records on its behalf asserts numbers it "
            f"did not measure, and the cycle then carries two accounts of one "
            f"run. The lead CONFIRMS the record exists."
        )


def test_the_owned_lead_surface_roster_is_every_lead_facing_surface() -> None:
    """Floor: a roster that silently emptied sweeps nothing.

    Named apart from the sweep above because a parametrize over an empty tuple
    collects zero cases and reports success -- the failure mode this module's
    tool-name derivation carries the same guard against.
    """
    assert len(_OWNED_LEAD_SURFACES) == 7, _OWNED_LEAD_SURFACES
    assert len(set(_OWNED_LEAD_SURFACES)) == 7, _OWNED_LEAD_SURFACES
    for path in _OWNED_LEAD_SURFACES:
        assert path.is_file(), f"{_rel(path)} does not exist"
    # start.md must still NAME the tool, or the sweep above is passing because
    # the subject left the file rather than because the rule is stated.
    assert "`Foundry-Stream`" in _flat(START_MD), (
        f"{_rel(START_MD)} no longer names `Foundry-Stream` at all. The rule "
        f"is that the AGENT records it -- a protocol that stops mentioning the "
        f"tool satisfies the absence sweep while telling the lead nothing."
    )


# ---------------------------------------------------------------------------
# The ordering token, quoted from the doors that emit it (fallout FR-055)
# ---------------------------------------------------------------------------
#
# D-089: `foundry_get_context` called `foundry_next_action` with no `caller`,
# so it took `LEAD_CALLER`, `is_lead` was True, and the write of
# `.next-action-called` fired on a SUB-AGENT's orienting read. That token is
# the sole precondition of `foundry_gate` and `foundry_mark_phase_complete`,
# so a lead could gate and transition having never called `Foundry-Next`.
# `agents/assayer.md` and `skills/prove/SKILL.md` both INSTRUCT a sub-agent to
# call `Foundry-Context` at F2, which is what made it routine rather than rare.
#
# Casting 2 carries the caller scope into that door. These two tests pin the
# LEAD-facing half: that both command files spell the sub-agent value the
# SERVER spells, and that the refusals they quote are the strings the doors
# really emit rather than paraphrases that drift free of them.


def test_the_caller_scope_prose_quotes_the_servers_own_tokens() -> None:
    """fallout FR-055: the sub-agent value is derived, never re-typed.

    Four surfaces say this word -- the wire enum, the tool description, the
    spawn-time instruction and the guard -- which is why the server names it
    once in ``SUBAGENT_CALLER``. A command file that spelled a fifth would tell
    a sub-agent to pass a value the guard does not accept, and the sub-agent
    would arm the lead's token exactly as before while looking compliant.
    """
    from foundry_mcp.tools.orchestration.guidance import (
        LEAD_CALLER,
        SUBAGENT_CALLER,
    )

    spelled = f"caller='{SUBAGENT_CALLER}'"
    for path in (START_MD, RESUME_MD):
        assert spelled in _flat(path), (
            f"{_rel(path)} does not spell {spelled!r}. The lead's two doors "
            f"into the loop both state which call arms the ordering token; a "
            f"file that names the argument without its server-spelled value "
            f"leaves a sub-agent guessing at the one word the guard reads."
        )

    assert f"take the `{LEAD_CALLER}` caller by default" in _flat(RESUME_MD), (
        f"{_rel(RESUME_MD)} no longer says its two calls take the "
        f"`{LEAD_CALLER}` caller by default. That is the half that tells a "
        f"RESUMING lead its own reads are the armed ones -- without it the "
        f"paragraph reads as though the lead had to pass something too."
    )


def test_start_md_quotes_the_refusals_the_doors_actually_emit() -> None:
    """fallout FR-055 and fallout GI-001: quoted from source, not paraphrased.

    Each literal below is asserted in the module that emits it AND in the
    prose that quotes it, so a reworded refusal fails on both sides at once
    rather than leaving the lead matching a sentence no door will ever print.
    The ``done``-gate literal is the D-070 half: an empty verdicts ledger is
    caught only at F6, by that count, after TEMPER and NYQUIST have already
    run against a tree nothing assayed.
    """
    orchestration = MCP_SRC / "tools" / "orchestration"
    gates_src = (orchestration / "gates.py").read_text(encoding="utf-8")
    transitions_src = (orchestration / "transitions.py").read_text(
        encoding="utf-8"
    )

    cases = (
        ("Must call Foundry-Next before any gate check", gates_src, "gates.py"),
        (
            "Must call Foundry-Next before phase transitions",
            transitions_src,
            "transitions.py",
        ),
        (
            "Only {verdict_count} verdicts but spec has {spec_count} "
            "requirements. {skipped} skipped.",
            gates_src,
            "gates.py",
        ),
    )

    flat = _flat(START_MD)
    for literal, source, module in cases:
        assert literal in source, (
            f"{module} no longer emits {literal!r}. The lead protocol quotes "
            f"it verbatim; re-word one side and the lead is watching for a "
            f"refusal string that door will never print."
        )
        assert literal in flat, (
            f"{_rel(START_MD)} no longer quotes {literal!r} from {module}. "
            f"Restore the quotation rather than paraphrasing it -- a lead "
            f"matches the refusal it is shown against the refusal it gets."
        )

    assert "Must call Foundry-Next before any gate check" in _flat(RESUME_MD), (
        f"{_rel(RESUME_MD)} no longer quotes the gate's ordering refusal. A "
        f"resuming lead is exactly the one who has not called `Foundry-Next` "
        f"yet, so this is the door where the refusal is most likely to be met."
    )
