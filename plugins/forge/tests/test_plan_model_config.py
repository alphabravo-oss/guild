"""Config-surface tests for plugins/forge/commands/plan.md.

Forge ships no MCP server, so its half of the model-select pilot is delivered
entirely as command *prose*: `${user_config.model}` is substituted into
plan.md's body at two independent spawn sites, and the main thread reads the
substituted value and branches. Nothing else in the repo checks that prose,
which is what let D-018 stand -- forge's half of the pilot had zero automated
coverage.

The two sites are:
  * R3.5, FINALIZATION SEQUENCE step 4.5 -- spawns `forge:spec-reviewer`
  * V3 R0 step 1 (V3 BROWNFIELD OVERRIDES) -- spawns `foundry:flow-mapper`

Both are located by CONTENT, never by line number: the arrow-delimited
substitution line is the anchor, and each site's block runs from that arrow
down to its "Record the spawn." instruction.

What is asserted per site:
  1. the arrow line delimits the literal `${user_config.model}` token
  2. the unconfigured case -- no `model` parameter at all, and never `model=""`
     (an unset option substitutes as an empty string; A-AUTO-014)
  3. the accepted-value case -- the five aliases passed as the spawn param
  4. the refusal case, whose message names exactly those five
  5. the spawn-record instruction, naming the same agent the site spawns

Plus plan.md's frontmatter uses the current `disable-model-invocation` key
rather than the legacy `hide-from-slash-command-tool` one (AC-016).

File-content assertions are the established pattern in this suite -- see
test_versioned_alignment.py, which checks cross-script literal alignment the
same way.
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
PLAN_MD = PLUGIN_ROOT / "commands" / "plan.md"

# The substituted value is wrapped in arrows so the lead can tell an empty
# substitution ("nothing between the arrows") apart from a missing line.
ARROW_LINE_RE = re.compile(r"^\s*→\s*(.*?)\s*←\s*$")
SUBSTITUTION_TOKEN = "${user_config.model}"

# A-014 "Aliases + inherit" -- the exact accepted set, no full claude-* IDs.
ACCEPTED_MODELS = frozenset({"opus", "sonnet", "haiku", "fable", "inherit"})

# The two spawns in plan.md that take their model from the option.
STEERABLE_AGENTS = frozenset({"spec-reviewer", "flow-mapper"})

RECORD_MARKER = "**Record the spawn.**"
# Both sites currently run 12 lines from arrow to record marker. The cap keeps
# a site's block from swallowing the rest of the file (and the other site's
# text) if the record instruction is ever deleted.
MAX_SITE_SPAN = 25

# Which agent this site spawns, read from the unconfigured-case bullet.
SPAWN_AGENT_RE = re.compile(
    r"Spawn `([a-z-]+)` with \*\*no `model` parameter at all\*\*"
)
# The `**Exactly one of `a`, `b`, ...:**` accepted-value bullet.
EXACTLY_ONE_OF_RE = re.compile(r"\*\*Exactly one of ([^:]+):\*\*")
# The user-facing refusal message's trailing value list.
ACCEPTED_VALUES_RE = re.compile(r"Accepted values are: ([^.]+)\.")
BACKTICKED_RE = re.compile(r"`([a-z-]+)`")


class Site:
    """One `${user_config.model}` substitution site in plan.md."""

    def __init__(self, arrow_lineno: int, arrow_text: str, block: str, has_record: bool):
        self.arrow_lineno = arrow_lineno  # 1-based, for failure messages
        self.arrow_text = arrow_text
        self.block = block
        self.has_record = has_record

    @property
    def label(self) -> str:
        return f"substitution site at plan.md:{self.arrow_lineno}"

    @property
    def agent(self) -> str | None:
        match = SPAWN_AGENT_RE.search(self.block)
        return match.group(1) if match else None


def _sites() -> list[Site]:
    """Locate every arrow-delimited substitution site by content."""
    lines = PLAN_MD.read_text(encoding="utf-8").split("\n")
    sites: list[Site] = []
    for index, line in enumerate(lines):
        match = ARROW_LINE_RE.match(line)
        if not match:
            continue
        end = None
        for offset in range(index + 1, min(index + 1 + MAX_SITE_SPAN, len(lines))):
            if RECORD_MARKER in lines[offset]:
                end = offset
                break
        # When the record instruction is missing, still hand the other tests a
        # bounded block so they report their own findings rather than erroring;
        # test_each_site_carries_the_spawn_record_instruction owns that failure.
        stop = end if end is not None else min(index + MAX_SITE_SPAN, len(lines) - 1)
        sites.append(
            Site(
                arrow_lineno=index + 1,
                arrow_text=match.group(1),
                block="\n".join(lines[index : stop + 1]),
                has_record=end is not None,
            )
        )
    return sites


def test_plan_md_exists() -> None:
    assert PLAN_MD.is_file(), (
        f"Expected forge's plan command at {PLAN_MD}. The model-config prose "
        f"for both steerable spawns lives in this file."
    )


def test_exactly_two_substitution_sites_exist() -> None:
    """R3.5 spawns spec-reviewer; V3 R0 spawns flow-mapper. No others.

    A third site would mean some other spawn silently started consuming the
    option; a missing site means one of the two steerable spawns lost its
    model plumbing and fell back to its frontmatter pin.
    """
    sites = _sites()
    assert len(sites) == 2, (
        f"Expected exactly 2 '→ {SUBSTITUTION_TOKEN} ←' substitution "
        f"sites in plan.md (R3.5 spec-reviewer + V3 R0 flow-mapper); found "
        f"{len(sites)} at line(s) {[s.arrow_lineno for s in sites]}."
    )


def test_each_site_delimits_the_substitution_token() -> None:
    """The arrows must wrap the literal token, nothing else.

    If the token were mistyped (or already replaced by a literal alias), the
    harness would substitute nothing and the lead would read a stale value.
    """
    for site in _sites():
        assert site.arrow_text == SUBSTITUTION_TOKEN, (
            f"{site.label}: expected the arrows to delimit exactly "
            f"{SUBSTITUTION_TOKEN!r}; got {site.arrow_text!r}."
        )


def test_each_site_carries_the_unconfigured_case() -> None:
    """Unconfigured -> spawn with no model parameter at all (GI-002, CT-002).

    Also covers the unsubstituted-placeholder case: on a build where the option
    is not declared, the literal `${...}` reaches the lead un-substituted
    (A-AUTO-011), and that must be treated as unconfigured too.
    """
    for site in _sites():
        assert "**Nothing between the arrows**" in site.block, (
            f"{site.label}: no '**Nothing between the arrows**' bullet. The "
            f"unconfigured case must be spelled out or the lead has no "
            f"instruction for an empty substitution."
        )
        assert "unsubstituted" in site.block, (
            f"{site.label}: the unconfigured bullet does not mention an "
            f"'unsubstituted' `${{...}}` placeholder. On a build where the "
            f"option is not declared the raw placeholder arrives instead of an "
            f"empty string, and it must resolve the same way."
        )
        assert "**no `model` parameter at all**" in site.block, (
            f"{site.label}: the unconfigured case does not instruct the lead to "
            f"spawn with **no `model` parameter at all**. 'Absence = no "
            f"override' (GI-002) requires omitting the parameter, not passing "
            f"a default."
        )


def test_each_site_forbids_passing_an_empty_model() -> None:
    """`model=""` is a malformed spawn, not a no-op (A-AUTO-014).

    An unset option substitutes as an empty string rather than leaving the
    placeholder, so the prose has to name this trap explicitly.
    """
    for site in _sites():
        assert 'Never pass `model=""`' in site.block, (
            f"{site.label}: missing the explicit 'Never pass `model=\"\"`' "
            f"prohibition. An unset option substitutes as an empty string "
            f"(A-AUTO-014), and passing it through produces a malformed spawn "
            f"rather than the intended no-override."
        )


def test_each_site_carries_the_accepted_value_case() -> None:
    """The five accepted aliases are passed through as the spawn's model."""
    for site in _sites():
        match = EXACTLY_ONE_OF_RE.search(site.block)
        assert match, (
            f"{site.label}: no '**Exactly one of `...`:**' bullet enumerating "
            f"the accepted values."
        )
        listed = BACKTICKED_RE.findall(match.group(1))
        assert len(listed) == len(ACCEPTED_MODELS), (
            f"{site.label}: the accepted-value bullet lists {len(listed)} "
            f"aliases {listed}; expected {len(ACCEPTED_MODELS)} with no "
            f"duplicates."
        )
        assert set(listed) == ACCEPTED_MODELS, (
            f"{site.label}: the accepted-value bullet lists {sorted(listed)}; "
            f"expected exactly {sorted(ACCEPTED_MODELS)} (A-014)."
        )
        assert "pass that value as the `model` parameter" in site.block, (
            f"{site.label}: the accepted-value bullet never says to pass the "
            f"value as the `model` parameter on the Agent spawn."
        )


def test_each_site_carries_the_refusal_case() -> None:
    """Anything outside the set is refused -- no guessing a nearest match."""
    for site in _sites():
        assert "**Anything else:** refuse." in site.block, (
            f"{site.label}: no '**Anything else:** refuse.' bullet. CT-001 "
            f"requires an unrecognised value to be refused, not coerced."
        )
        assert "do not guess at a nearest match" in site.block, (
            f"{site.label}: the refusal bullet does not forbid guessing at a "
            f"nearest match, so a typo could still reach a spawn."
        )


def test_refusal_message_names_exactly_the_five_accepted_values() -> None:
    """The error the user sees must enumerate the accepted set (CT-001)."""
    for site in _sites():
        match = ACCEPTED_VALUES_RE.search(site.block)
        assert match, (
            f"{site.label}: the refusal message does not carry an 'Accepted "
            f"values are: ...' list. CT-001 requires the refusal to name the "
            f"accepted set so the user can correct the option."
        )
        listed = [value.strip() for value in match.group(1).split(",")]
        assert len(listed) == len(ACCEPTED_MODELS), (
            f"{site.label}: refusal message names {len(listed)} values "
            f"{listed}; expected {len(ACCEPTED_MODELS)} with no duplicates."
        )
        assert set(listed) == ACCEPTED_MODELS, (
            f"{site.label}: refusal message names {sorted(listed)}; expected "
            f"exactly {sorted(ACCEPTED_MODELS)} (A-014)."
        )


def test_each_site_carries_the_spawn_record_instruction() -> None:
    """Forge writes no spawns.log, so the transcript line IS the record.

    Foundry records steerable spawns in spawns.log via foundry_spawn.py; forge
    ships no MCP server and has no equivalent, so NFR-001 "Observable in
    transcripts" is satisfied only by the lead stating the resolved outcome
    before spawning.
    """
    for site in _sites():
        assert site.has_record, (
            f"{site.label}: no '{RECORD_MARKER}' instruction within "
            f"{MAX_SITE_SPAN} lines of the substitution. Forge ships no MCP "
            f"server and writes no spawn log, so this transcript line is the "
            f"only record of which model the agent was asked to run on "
            f"(NFR-001 'Observable in transcripts')."
        )
        assert "state the resolved outcome" in site.block, (
            f"{site.label}: the record instruction does not tell the lead to "
            f"state the resolved outcome before spawning."
        )


def test_spawn_record_names_the_agent_that_site_spawns() -> None:
    """Guards the copy-paste hazard between the two near-identical blocks.

    The two record instructions are byte-identical apart from the agent name,
    so a copy-paste would leave one site recording the other's agent -- a
    transcript that names the wrong agent is worse than no record at all.
    """
    for site in _sites():
        agent = site.agent
        assert agent is not None, (
            f"{site.label}: could not determine which agent this site spawns; "
            f"expected a 'Spawn `<agent>` with **no `model` parameter at all**' "
            f"clause in the unconfigured bullet."
        )
        assert f"spawning {agent} with model=" in site.block, (
            f"{site.label}: spawns {agent!r} but its record instruction has no "
            f"'spawning {agent} with model=<value>' form. A record naming a "
            f"different agent is a copy-paste between the two blocks."
        )
        assert f"spawning {agent} with no model parameter" in site.block, (
            f"{site.label}: spawns {agent!r} but its record instruction has no "
            f"'spawning {agent} with no model parameter (option unset)' form, "
            f"so the unconfigured path would leave no transcript record."
        )


def test_the_two_sites_cover_both_steerable_agents() -> None:
    """One site per steerable agent -- spec-reviewer and flow-mapper."""
    agents = [site.agent for site in _sites()]
    assert set(agents) == STEERABLE_AGENTS, (
        f"Expected plan.md's two substitution sites to spawn exactly "
        f"{sorted(STEERABLE_AGENTS)}; got {agents}. `forge:researcher` keeps "
        f"its own frontmatter pin and must not become steerable here."
    )


def test_frontmatter_uses_the_current_disable_model_invocation_key() -> None:
    """AC-016: the legacy key is dead and hides nothing from the tool."""
    text = PLAN_MD.read_text(encoding="utf-8")
    assert re.search(r"^disable-model-invocation:", text, re.MULTILINE), (
        f"plan.md frontmatter does not declare `disable-model-invocation`. "
        f"That is the current key (AC-016); without it the command is not "
        f"actually hidden from the SlashCommand tool."
    )
    assert "hide-from-slash-command-tool" not in text, (
        f"plan.md still carries the legacy `hide-from-slash-command-tool` key. "
        f"It appears nowhere in current Claude Code docs and hides nothing; "
        f"AC-016 moves all nine command files to `disable-model-invocation`."
    )
