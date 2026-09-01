"""Prose pins for the protocol text that lives only in agent/command markdown.

Why this module exists
----------------------
Three of this effort's rulings ship as *prose* and nothing else. There is no
Python to unit-test, because the artifact IS the instruction a spawned agent
reads:

- **GI-001 / AC-003** — the observation/defect split must be in the four stream
  agent files and ``commands/start.md``'s F2 roster on a **fresh checkout**,
  with no per-run configuration. A per-run directive or a seeded injection
  would satisfy a behavioural test while violating the invariant, so the only
  honest check is "read the committed file and look".
- **FR-011 / CT-005 / AC-017** — the pathspec commit form in
  ``agents/teammate.md``. Another casting proves the *git mechanics* with a
  real-git harness (``test_commit_guard.py``); nothing there proves a teammate
  is ever **told** to use them. This module is that half.
- **FR-016** — the retired ``scripts/foundry.sh`` allow-list entries. A
  deletion is invisible to every positive test ever written, so it needs an
  explicit absence assertion or it silently comes back on the next edit.

Prose rots differently from code: it does not fail to compile, it does not
throw, and a reviewer skimming a 750-line agent file will not notice that one
bullet lost its absolute. Every assertion below is written so that reverting
the prose turns it RED, and each carries a message naming the ruling it
defends so the next reader knows what they are about to un-ship.

Why substring assertions and not a parser
-----------------------------------------
The pinned strings are load-bearing English, not structured data. The register
these files use (``- **Bolded imperative.** ...`` closing on an absolute) has
no schema to validate against, and a fuzzy check — "the word observation
appears somewhere" — would pass on prose that says the opposite. Exact
substrings on the clauses that carry the ruling are the tightest available
guard. Where four files must agree on a term (the ledger filename, the
denylist), the assertion is parametrised across all four so a partial edit
that fixes three of them fails.

Deliberately NOT asserted here: the exact wording of each split paragraph.
Each of the four files states the split in its own voice (assayer's verdict
voice, tracer's wiring voice, flow-tracer's terse chain voice,
research-auditor's deviation voice), which is required — pasting one identical
paragraph into four files was explicitly rejected. So the pins are on the
clauses that carry the *ruling*, never on a whole sentence.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.tools import foundry_orchestrator as orch
from foundry_mcp.tools import foundry_spawn as fs
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.foundry_validate import foundry_validate_castings

# D-048: the vocabulary assertions below READ the real enum rather than
# re-typing it. A hard-coded tuple in a test is a seventh copy of a closed
# vocabulary -- it passes while vocab.py grows a member the markdown never
# learns, and it passes while the markdown advertises a value the server
# refuses. D-045 (skills/temper/SKILL.md shipping `source: "temper-sweep"`,
# hard-refused at the MCP boundary and dropped before reaching the ledger) is
# what that hole already cost. Importing is what makes drift on EITHER side
# fail here.
#
# ``pythonpath = ["src"]`` in pyproject.toml puts the package on sys.path at
# pytest startup, so this import needs no path surgery; test_vocab.py imports
# the same module the same way.

# tests -> mcp-server -> foundry -> plugins -> repo root.
# Mirrors test_agent_frontmatter_parse.py:48 rather than hardcoding an absolute
# path, so the suite runs from any checkout.
REPO_ROOT = Path(__file__).resolve().parents[4]
FOUNDRY_ROOT = REPO_ROOT / "plugins" / "foundry"

AGENTS = FOUNDRY_ROOT / "agents"
COMMANDS = FOUNDRY_ROOT / "commands"
REFERENCES = FOUNDRY_ROOT / "references"

ASSAYER = AGENTS / "assayer.md"
TRACER = AGENTS / "tracer.md"
FLOW_TRACER = AGENTS / "flow-tracer.md"
RESEARCH_AUDITOR = AGENTS / "research-auditor.md"
TEAMMATE = AGENTS / "teammate.md"
START_MD = COMMANDS / "start.md"
RESUME_MD = COMMANDS / "resume.md"
LEAD_DISCIPLINE = REFERENCES / "lead-discipline.md"

# The four F2 INSPECT streams that file findings into the defect ledger. The
# split is a property of all four together: a stream still filing comment prose
# as a defect re-opens the loop this effort exists to close.
STREAM_AGENTS = (ASSAYER, TRACER, FLOW_TRACER, RESEARCH_AUDITOR)

# Locked cross-casting contract: another casting ships the ledger and the
# Foundry-Defect / Foundry-Sync refusal that routes findings into it. The four
# agent files must name it byte-identically or a stream writes to a file that
# does not exist.
OBSERVATIONS_LEDGER = "observations.json"

# Assembled from two halves so this test module is not itself a grep hit when
# someone searches the repo for "does foundry ever permit a hook bypass?".
HOOK_BYPASS_FLAG = "--no-" + "verify"


def _read(path: Path) -> str:
    """Read a pinned prose file, failing loudly if it moved or was deleted."""
    assert path.is_file(), (
        f"{path.relative_to(REPO_ROOT)} does not exist. If the file was "
        f"renamed, update this module's path constants -- do not delete the "
        f"assertions, the prose they pin is still required."
    )
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def _flat(path: Path) -> str:
    """Read a prose file with every run of whitespace collapsed to one space.

    Markdown wraps a sentence across source lines wherever the author's column
    limit fell, and where that break lands is not a property this module has
    any business pinning. Matching a multi-word phrase against the raw text
    makes the assertion fail on a reflow that changed no words -- a false
    finding of exactly the kind FR-004 exists to stop. Phrase-level pins read
    the flattened text; single-token and code-span pins can use ``_read``.
    """
    return " ".join(_read(path).split())


# ---------------------------------------------------------------------------
# GI-001 / FR-003 / AC-003 -- the observation/defect split, baked into files
# ---------------------------------------------------------------------------


def test_the_pinned_prose_files_all_exist() -> None:
    """Floor check: every assertion below is vacuous if the corpus is empty."""
    for path in (
        *STREAM_AGENTS,
        TEAMMATE,
        START_MD,
        RESUME_MD,
        LEAD_DISCIPLINE,
    ):
        assert path.is_file(), f"missing pinned prose file: {_rel(path)}"


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_routes_comment_prose_to_observations(path: Path) -> None:
    """AC-003: the split is in the committed file, not injected per run."""
    text = _read(path)
    assert "Comment-prose findings are observations, not defects." in text, (
        f"{_rel(path)} lost the observation/defect split. GI-001 requires it "
        f"in all four stream agent files -- a per-run directive or seeded "
        f"injection does NOT satisfy it."
    )
    assert OBSERVATIONS_LEDGER in text, (
        f"{_rel(path)} does not name the {OBSERVATIONS_LEDGER} ledger. The "
        f"filename is a locked cross-casting contract; a stream cannot route a "
        f"finding to a ledger it cannot name."
    )
    assert "`defects` array" in text, (
        f"{_rel(path)} no longer contrasts the observations ledger with the "
        f"`defects` array, so the split does not say where findings go."
    )


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_states_the_refusal_is_server_side(path: Path) -> None:
    """AC-001: filing comment prose as a defect is refused, not discouraged."""
    text = _read(path)
    assert "`Foundry-Defect` and `Foundry-Sync` refuse it as a defect server-side." in text, (
        f"{_rel(path)} no longer states that the refusal is server-side. A "
        f"split phrased as advice is a split streams can talk themselves out of."
    )


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_states_the_never_demote_denylist_absolutely(path: Path) -> None:
    """AC-002: the denylist is an absolute in every one of the four files."""
    text = _read(path)
    assert "**The never-demote denylist is absolute.**" in text, (
        f"{_rel(path)} lost the never-demote denylist. Without it the split is "
        f"a demotion channel with no floor."
    )
    for claim in (
        "security-property claim",
        "spec-required-behaviour claim",
        "unresolvable cite",
        "anything that is not a comment",
    ):
        assert claim in text, (
            f"{_rel(path)}'s denylist no longer names '{claim}'. All four "
            f"denylist members are required (AC-002)."
        )
    assert "NEVER be recorded as an observation" in text, (
        f"{_rel(path)}'s denylist was softened below an absolute."
    )
    assert "audit tripwire" in text, (
        f"{_rel(path)} no longer fires the audit tripwire on a denylist "
        f"demotion attempt (AC-002)."
    )


# The behavioural/security defect standard must NOT weaken. Truth 2 of this
# casting names these absolutes explicitly: the split moves comment prose out
# of the ledger, it does not create a severity tier and it grants no stream
# discretion to skip a behavioural or security finding.
UNWEAKENED_ABSOLUTES = {
    ASSAYER: (
        "**EVERY non-VERIFIED verdict is a defect.**",
        "No exceptions, no deferrals, no \"deferred to next sprint.\"",
        "**Missing prerequisites are defects.**",
        "**No \"deferred\" or \"out of scope\" verdicts.**",
        "**No severity classification.**",
    ),
    TRACER: (
        "**EVERY non-WIRED verdict is a defect.**",
        "No exceptions, no deferrals, no \"out of scope.\"",
        "**Missing prerequisites are defects.**",
        "**`NOT_VERIFIED` is a defect, not a deferral.**",
        "**No severity classification.**",
    ),
    FLOW_TRACER: (
        "**`NOT_VERIFIED` is a defect, not a deferral.**",
        "**NEVER emit `SOURCED` for a packet you did not actually walk.**",
        "**No severity tiers.**",
    ),
    RESEARCH_AUDITOR: (
        "All deviations are defects.",
        "**No severity classification.**",
    ),
}


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_the_split_did_not_soften_any_existing_absolute(path: Path) -> None:
    """Truth 2: the split is a channel change, never a standard change."""
    text = _read(path)
    for absolute in UNWEAKENED_ABSOLUTES[path]:
        assert absolute in text, (
            f"{_rel(path)} lost the absolute {absolute!r}. The "
            f"observation/defect split must not soften, delete, or qualify any "
            f"existing defect-standard absolute -- that is the exact failure "
            f"the never-demote denylist exists to prevent."
        )


def test_start_md_f2_roster_carries_the_split() -> None:
    """GI-001: start.md's F2 roster names the split and the denylist."""
    text = _read(START_MD)
    assert "### F2: INSPECT" in text, "start.md lost its F2 INSPECT roster heading"
    assert (
        "**Every INSPECT stream files comment-prose findings as observations, "
        "not defects.**" in text
    ), (
        "start.md's F2 roster no longer states the observation/defect split. "
        "GI-001 requires the roster to carry it alongside the four agent files."
    )
    assert OBSERVATIONS_LEDGER in text, (
        f"start.md's F2 roster does not name the {OBSERVATIONS_LEDGER} ledger."
    )
    assert "**The never-demote denylist is absolute:**" in text, (
        "start.md's F2 roster does not name the never-demote denylist."
    )
    assert "no per-run configuration" in text, (
        "start.md's F2 roster no longer states that the split needs no per-run "
        "configuration (AC-003)."
    )


# ---------------------------------------------------------------------------
# FR-011 / FR-026 / CT-005 / AC-017 -- the pathspec commit protocol
# ---------------------------------------------------------------------------


def test_teammate_commit_protocol_mandates_a_pathspec() -> None:
    """CT-005: the commit form is pathspec-scoped over the casting's key_files."""
    text = _read(TEAMMATE)
    assert "### Step 2: Commit with an explicit pathspec" in text, (
        "teammate.md's commit step is no longer the pathspec step. A bare "
        "`git commit` takes the ENTIRE shared index (FR-011)."
    )
    assert "Name your casting's `key_files` after a `--` separator." in text, (
        "teammate.md no longer tells the teammate which paths to name."
    )
    assert 'git commit -m "feat(foundry): implement login endpoint with bcrypt password hashing" -- \\' in text, (
        "teammate.md's worked commit example lost its `--` pathspec separator."
    )


def test_teammate_commit_protocol_forbids_the_bare_commit() -> None:
    """FR-011: the bare commit is named as the failure, not merely omitted."""
    text = _read(TEAMMATE)
    assert '**NEVER run a bare `git commit -m "..."`.**' in text, (
        "teammate.md no longer forbids the bare commit. Omitting the pathspec "
        "is the exact mechanism by which one teammate captures a peer's staged "
        "work (OT-005)."
    )
    assert "commits the ENTIRE index by git's documented default" in text, (
        "teammate.md no longer explains WHY a bare commit is unsafe. The "
        "mechanism is non-obvious -- teammates who do not know it will drop the "
        "pathspec as noise."
    )


def test_teammate_commit_protocol_covers_new_files_deletes_and_renames() -> None:
    """FR-026: a pathspec commit needs staging for new paths, and both sides
    of a rename, or it silently ships a partial change."""
    text = _read(TEAMMATE)
    assert "**Every new file must be `git add`ed here.**" in text, (
        "teammate.md no longer says new files must be staged before the "
        "pathspec commit. An untracked path named in a pathspec is silently "
        "omitted -- the commit succeeds and the file is missing."
    )
    assert "**Name deletes and renames in the pathspec too.**" in text, (
        "teammate.md no longer covers deletes and renames in the pathspec."
    )
    assert "A rename is two paths" in text, (
        "teammate.md no longer says a rename needs both paths named; a "
        "one-sided rename records half the change."
    )


def test_teammate_commit_protocol_codifies_the_no_stash_rule() -> None:
    """AC-017: no-stash is codified, with the reason it is not a workaround."""
    text = _read(TEAMMATE)
    assert "**Never run `git stash`, in any form.**" in text, (
        "teammate.md lost the no-stash rule (AC-017). Stash is the obvious "
        "wrong answer to a dirty shared tree and will be reinvented without it."
    )
    assert "`git stash --keep-index`" in text, (
        "teammate.md no longer rules out the --keep-index variant "
        "specifically -- it is the form that silently drops partially-staged "
        "hunks, so naming plain `git stash` alone leaves the trap open."
    )


def test_teammate_protocol_offers_no_hook_bypass_anywhere() -> None:
    """AC-017: no protocol path requires a pre-commit-hook bypass.

    This is a whole-file absence assertion, not a scoped one. The flag is
    dangerous precisely because it reads as a reasonable escape hatch in
    passing, so the token must not appear in this file at all -- including
    inside an example, a caveat, or a prohibition that quotes it. The rule
    against bypassing the hook is therefore written WITHOUT naming the flag
    (see "Never skip the pre-commit hook"), which is what lets this assertion
    stay absolute.
    """
    text = _read(TEAMMATE)
    assert HOOK_BYPASS_FLAG not in text, (
        "teammate.md now contains a pre-commit-hook bypass flag. No protocol "
        "path may require or offer one (AC-017): the shipped guard evaluates "
        "staged content only, so a correct pathspec commit always passes it. "
        "If a guard fires, the fix is the staging, never the bypass."
    )
    assert "**Never skip the pre-commit hook.**" in text, (
        "teammate.md lost the rule forbidding hook bypass."
    )
    assert "judges staged content only (`git diff --cached`)" in text, (
        "teammate.md no longer explains that the guard judges the index, which "
        "is the reason a bypass is never needed (GI-002)."
    )


def test_teammate_still_forbids_git_add_dot() -> None:
    """The pathspec fixes the COMMIT boundary; the staging boundary still
    needs its own prohibition, so the pre-existing rule must survive."""
    text = _read(TEAMMATE)
    assert "**NEVER** use `git add .` or `git add -A`." in text, (
        "teammate.md lost the `git add .` prohibition. Pathspec commits fix "
        "the commit boundary, not the staging boundary -- both rules are "
        "required."
    )


# ---------------------------------------------------------------------------
# FR-009 / AC-014 -- the GRIND adjacent-path step
# ---------------------------------------------------------------------------


def test_teammate_grind_protocol_has_the_adjacent_path_step() -> None:
    """AC-014: both declarations exist BEFORE the Foundry-Fix call."""
    text = _read(TEAMMATE)
    assert "### Step 7: DECLARE — the adjacent paths, and a test that drives one" in text, (
        "teammate.md's GRIND protocol lost the adjacent-path step (AC-014). "
        "Without it a teammate reaches Foundry-Fix with nothing to declare and "
        "the server refuses the transition."
    )
    assert "**The adjacent-path statement.**" in text, (
        "teammate.md no longer requires the adjacent-path statement (FR-009)."
    )
    assert "**The adjacent-path test reference.**" in text, (
        "teammate.md no longer requires the adjacent-path test reference "
        "(FR-009 / FR-010)."
    )


def test_teammate_adjacent_path_statement_names_all_three_axes() -> None:
    """FR-009: who else calls this / what else transitions here / what runs
    concurrently -- all three, or the statement has a blind spot."""
    text = _read(TEAMMATE)
    for axis in (
        "**Who else calls this**",
        "**What else transitions here**",
        "**What runs concurrently**",
    ):
        assert axis in text, (
            f"teammate.md's adjacent-path statement no longer asks for "
            f"{axis!r}. FR-009 names all three axes."
        )


def test_teammate_adjacent_path_test_must_drive_a_different_path() -> None:
    """FR-010: the defect's own regression test does not satisfy the rule."""
    text = _read(TEAMMATE)
    assert "drives a **NAMED** adjacent path" in text, (
        "teammate.md no longer requires the test to drive a NAMED adjacent "
        "path (FR-010)."
    )
    assert "The defect's own regression test does not satisfy this." in text, (
        "teammate.md no longer rules out the defect's own regression test. "
        "Without that sentence the requirement is trivially satisfiable by the "
        "test the teammate already wrote, which proves nothing about adjacent "
        "paths."
    )


def test_teammate_states_foundry_fix_refuses_without_the_declarations() -> None:
    """FR-009: the refusal is server-side and names what is missing."""
    text = _read(TEAMMATE)
    assert "`Foundry-Fix`" in text, (
        "teammate.md never mentions Foundry-Fix, so the GRIND protocol does "
        "not connect to the tool whose required fields it exists to satisfy."
    )
    assert "the server refuses the transition without them and names which one is missing" in text, (
        "teammate.md no longer states that Foundry-Fix refuses without the "
        "declarations (FR-009)."
    )


# ---------------------------------------------------------------------------
# FR-016 -- the retired bash twin's allow-list entries (absence assertions)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("path", (START_MD, RESUME_MD), ids=lambda p: p.name)
def test_command_allow_lists_no_longer_permit_the_bash_twin(path: Path) -> None:
    """FR-016 / GI-003: the MCP server is the single implementation.

    An absence assertion, because a deletion is invisible to every positive
    test: nothing else in this suite would notice the entry coming back on a
    later edit, and a live allow-list entry is what lets the retired bash twin
    be invoked and drift.
    """
    text = _read(path)
    assert "scripts/foundry.sh" not in text, (
        f"{_rel(path)} still allow-lists the retired bash twin "
        f"scripts/foundry.sh. GI-003: the MCP server is the single "
        f"implementation -- a second live implementation drifts."
    )


def test_start_md_installs_the_commit_guard() -> None:
    """GI-002: every target repo a run touches gets the shipped guard."""
    text = _read(START_MD)
    assert '"${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-guard.sh"' in text, (
        "start.md no longer invokes the commit-guard installer (GI-002). "
        "Without the install step the guard ships but is never installed, so "
        "target repos keep whatever hook they had."
    )
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/pre-commit-guard.sh" in text, (
        "start.md no longer names the guard asset the installer places."
    )
    assert "`git diff --cached`" in text, (
        "start.md no longer states that the guard judges the index only. A "
        "working-tree guard (`git diff HEAD`) fires on a peer's unstaged WIP, "
        "which is the GI-002 violation."
    )


def test_start_md_references_the_installer_through_the_plugin_root() -> None:
    """Convention: a shipped asset is referenced through ${CLAUDE_PLUGIN_ROOT},
    never an absolute path -- the plugin cache is version-namespaced, so an
    absolute path pins one installed version and breaks on upgrade."""
    text = _read(START_MD)
    for line in text.splitlines():
        if "install-commit-guard.sh" in line or "pre-commit-guard.sh" in line:
            assert "${CLAUDE_PLUGIN_ROOT}" in line, (
                f"guard asset referenced without ${{CLAUDE_PLUGIN_ROOT}}: {line!r}"
            )


# ---------------------------------------------------------------------------
# FR-011 rationale -- lead-discipline.md
# ---------------------------------------------------------------------------


def test_lead_discipline_explains_pathspec_commits() -> None:
    """FR-011: the rationale file carries WHY, the mechanics stay in
    teammate.md."""
    text = _read(LEAD_DISCIPLINE)
    assert "## Why commits are pathspec-scoped" in text, (
        "lead-discipline.md has no pathspec commit rationale (FR-011)."
    )
    assert "It does not make the *index* safe to share" in text, (
        "lead-discipline.md no longer explains the gap between disjoint file "
        "ownership and a shared index -- the reason the failure was invisible."
    )


def test_lead_discipline_keeps_the_no_worktrees_rule() -> None:
    """Out of Scope: the shared-tree model and the no-worktrees rule stay.

    The pathspec protocol is what makes the shared tree safe; it is emphatically
    not a step toward per-teammate worktrees, and a future reader must not be
    able to mistake it for one.
    """
    text = _read(LEAD_DISCIPLINE)
    assert "## Why no worktrees" in text, (
        "lead-discipline.md lost the no-worktrees section. The shared-tree "
        "model is explicitly Out of Scope for change -- pathspec commits make "
        "the shared tree safe, they do not replace it with worktrees."
    )
    assert 'no `isolation: "worktree"` when spawning agents' in text, (
        "lead-discipline.md's no-worktrees rule was altered."
    )


# ---------------------------------------------------------------------------
# FR-004 -- symbol-authoritative cite policy in this casting's prose sites
# ---------------------------------------------------------------------------

# Every verifier that emits a cite must state that the symbol decides validity.
# Without this, a drifted line number reads as a broken cite and the stream
# files a defect for it -- the loop this effort exists to close.
#
# D-046: FLOW_TRACER was the one STREAM_AGENTS member missing from this tuple,
# with no comment explaining the omission -- so when GRIND-1 converted the
# other three stream agents, nothing failed to report that flow-tracer.md had
# been skipped. It is a live F2 roster member that files into the `defects`
# array and feeds Foundry-Sync, so the cite-refresh prohibition was unenforced
# on a defect-filing stream. The containment test below is what stops a
# future fifth stream agent from slipping through the same gap.
SYMBOL_AUTHORITATIVE_SITES = (
    ASSAYER,
    TRACER,
    FLOW_TRACER,
    RESEARCH_AUDITOR,
    TEAMMATE,
)


def test_every_defect_filing_stream_states_the_cite_policy() -> None:
    """D-046's root cause: the tuple above was hand-maintained.

    A stream agent that files defects but never learned the symbol-authoritative
    rule will file a drifted line number as a defect, which is precisely the
    loop FR-004 exists to close. Membership is therefore not a judgement call:
    every STREAM_AGENTS member is a cite site, and this asserts the two lists
    cannot drift apart again.
    """
    missing = sorted(_rel(p) for p in set(STREAM_AGENTS) - set(SYMBOL_AUTHORITATIVE_SITES))
    assert not missing, (
        f"{missing} file into the defect ledger but are absent from "
        f"SYMBOL_AUTHORITATIVE_SITES, so nothing checks that they state the "
        f"symbol-authoritative cite rule. Add them to the tuple and give each "
        f"one the rule in its own voice -- do not shrink this assertion."
    )


@pytest.mark.parametrize("path", SYMBOL_AUTHORITATIVE_SITES, ids=lambda p: p.name)
def test_cite_sites_use_the_symbol_form(path: Path) -> None:
    """FR-004 placement rule: cites are `path#Symbol`."""
    text = _read(path)
    assert "`path#Symbol`" in text, (
        f"{_rel(path)} no longer uses the `path#Symbol` cite form (FR-004)."
    )


@pytest.mark.parametrize("path", SYMBOL_AUTHORITATIVE_SITES, ids=lambda p: p.name)
def test_cite_sites_state_the_symbol_is_authoritative(path: Path) -> None:
    """FR-004 validity rule: a resolving symbol is valid despite a stale line."""
    text = _read(path)
    assert "symbol is authoritative" in text, (
        f"{_rel(path)} no longer states that the symbol is authoritative. "
        f"Without it a drifted line hint reads as a broken cite."
    )
    assert "cite-refresh sweep" in text, (
        f"{_rel(path)} no longer prohibits unprompted cite-refresh sweeps "
        f"(FR-004). A sweep manufactures churn across the whole tree."
    )


def test_teammate_citation_template_cites_symbols_not_line_ranges() -> None:
    """FR-004: the Requirement Citations template was all line-ranges."""
    text = _read(TEAMMATE)
    assert "- US-N: src/api/auth/login.ts#loginHandler" in text, (
        "teammate.md's Requirement Citations template no longer demonstrates "
        "the `path#Symbol` form (FR-004)."
    )
    assert "src/api/auth/login.ts:42-78" not in text, (
        "teammate.md's citation template still shows a line-range cite. The "
        "template is what teammates copy, so a line-range example reintroduces "
        "line-range cites across every casting."
    )


def test_teammate_states_where_line_hints_are_still_permitted() -> None:
    """FR-004 placement rule: line hints live only in commit-pinned artifacts."""
    text = _read(TEAMMATE)
    assert "commit-pinned run artifact" in text, (
        "teammate.md no longer says where a line hint IS permitted. The rule "
        "is a placement rule, not a ban -- omitting the permitted case makes "
        "it read as a blanket prohibition and evidence logs lose their cites."
    )
    assert "a moved line alone produces no finding of any kind" in text, (
        "teammate.md no longer states that a moved line produces no finding."
    )


# ---------------------------------------------------------------------------
# GRIND cycle 1 -- D-005, D-011, D-015, D-016, D-019, D-020, D-026, D-035
# ---------------------------------------------------------------------------

SKILLS = FOUNDRY_ROOT / "skills"

# The three verification skills that emit code cites. sight/SKILL.md is
# deliberately absent: it audits a running browser, so its evidence is
# screenshots and console output, never a source location.
CITE_EMITTING_SKILLS = (
    SKILLS / "trace" / "SKILL.md",
    SKILLS / "prove" / "SKILL.md",
    SKILLS / "temper" / "SKILL.md",
)

# A cite carrying a line number, in any of the languages these files use for
# worked examples. Built at import time so a new example in a new language is
# still caught.
_LINE_CITE_RE = re.compile(r"\.(?:go|ts|tsx|js|jsx|py|html|sh):\d+")


def test_the_cite_emitting_skills_all_exist() -> None:
    """Floor check: the D-015 assertions are vacuous if the corpus is empty."""
    for path in CITE_EMITTING_SKILLS:
        assert path.is_file(), f"missing pinned skill file: {_rel(path)}"


@pytest.mark.parametrize("path", CITE_EMITTING_SKILLS, ids=lambda p: p.parent.name)
def test_skills_mandate_the_symbol_cite_form(path: Path) -> None:
    """D-015 / FR-004: the skills tree had a zero-line diff against FR-004."""
    text = _read(path)
    assert "`path#Symbol`" in text, (
        f"{_rel(path)} no longer mandates the `path#Symbol` cite form. FR-004's "
        f"scope is 'agents/skills' -- converting the agent files alone leaves "
        f"the same loop open on every standalone skill invocation."
    )
    assert "file:line" not in text, (
        f"{_rel(path)} still mandates a `file:line` cite somewhere. The whole "
        f"point of FR-004 is that no verifier judges the line component."
    )


@pytest.mark.parametrize("path", CITE_EMITTING_SKILLS, ids=lambda p: p.parent.name)
def test_skills_state_the_symbol_is_authoritative(path: Path) -> None:
    """D-015: the validity rule, not just the placement rule."""
    text = _flat(path)
    assert "symbol is authoritative" in text, (
        f"{_rel(path)} does not state that the symbol decides validity. Without "
        f"it a drifted line reads as a broken cite and the skill files a finding."
    )
    assert "a moved line alone produces no finding of any kind" in text, (
        f"{_rel(path)} lost the no-finding-for-a-moved-line rule (FR-004)."
    )
    assert "cite-refresh sweep" in text, (
        f"{_rel(path)} no longer prohibits unprompted cite-refresh sweeps."
    )
    assert "commit-pinned run artifact" in text, (
        f"{_rel(path)} does not say where a line hint IS still permitted, so "
        f"the placement rule reads as a blanket ban."
    )


@pytest.mark.parametrize("path", CITE_EMITTING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_examples_carry_no_line_cites(path: Path) -> None:
    """D-016's sibling: a worked example is what a reader actually copies."""
    stale = _LINE_CITE_RE.findall(_read(path))
    assert not stale, (
        f"{_rel(path)} still shows line-numbered cite(s) {stale} in a worked "
        f"example. Prose mandating `path#Symbol` beside an example emitting "
        f"`path:line` is the contradiction D-016 was filed for -- the example wins."
    )


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_stream_agent_json_examples_carry_no_line_cites(path: Path) -> None:
    """D-016: the normative JSON examples contradicted their own mandate."""
    stale = _LINE_CITE_RE.findall(_read(path))
    assert not stale, (
        f"{_rel(path)}'s normative JSON example still emits {stale}. The file "
        f"mandates `path#Symbol` in its Rules block, so a line-numbered example "
        f"tells a stream to do the opposite of what the same file just required."
    )


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_stream_agents_resolve_the_example_carve_out_explicitly(path: Path) -> None:
    """D-016: prose and examples must visibly agree, not merely not conflict."""
    text = _read(path)
    assert "Every cite in that shape is `path#Symbol`" in text, (
        f"{_rel(path)} does not state that its own output examples follow the "
        f"cite mandate. D-016 is a CONTRADICTION defect: resolving it requires "
        f"the file to say which rule its examples obey."
    )
    assert "carve-out that permits a line hint does not reach" in text, (
        f"{_rel(path)} no longer rules on whether a findings record qualifies "
        f"for the commit-pinned run-artifact carve-out. Leaving it unstated is "
        f"what let the examples and the mandate drift apart."
    )


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_instructs_the_class_declaration(path: Path) -> None:
    """D-011 / FR-007: no producer existed for the defect `class` field."""
    text = _read(path)
    assert "share a root cause.**" in text, (
        f"{_rel(path)} has no class-declaration instruction. Without a producer "
        f"in every stream, escalation (ST-002) always falls back to the "
        f"clustering heuristic and a declared class never exists."
    )
    assert "`class` field" in text, (
        f"{_rel(path)} does not name the `class` field the record carries."
    )
    assert "`defect_class`" in text, (
        f"{_rel(path)} does not name the `defect_class` parameter Foundry-Defect "
        f"exposes, so a stream cannot carry the class it was told to declare."
    )
    assert "spelled identically" in text, (
        f"{_rel(path)} does not require the class string to be spelled "
        f"identically across instances. Escalation counts a class by exact "
        f"string, so an unpinned spelling silently never reaches three cycles."
    )


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_json_shape_carries_the_class_key(path: Path) -> None:
    """D-011: the instruction is inert if the output shape has no slot."""
    text = _read(path)
    assert '"class":' in text, (
        f"{_rel(path)}'s findings JSON shape has no `class` key, so a stream "
        f"told to declare a class has nowhere to put it (D-011)."
    )


def test_assayer_reconciles_its_every_verdict_absolute_with_the_split() -> None:
    """D-026: an internal contradiction three lines wide."""
    text = _read(ASSAYER)
    assert "The observation split below removes nothing from that list" in text, (
        "assayer.md's 'EVERY non-VERIFIED verdict is a defect' still carries no "
        "reconciling carve-out for the observation split, unlike its three peer "
        "files. The absolute and the split bullet three lines below read as a "
        "contradiction, and a stream resolves contradictions in its own favour."
    )
    assert "is not a comment" in text, (
        "assayer.md's reconciliation does not say WHY the split takes nothing "
        "out of the verdict list -- because a requirement is not a comment."
    )
    # The reconciliation must not become a demotion route.
    assert "**The never-demote denylist is absolute.**" in text, (
        "assayer.md lost the never-demote denylist while reconciling D-026. The "
        "carve-out must not weaken the standard it reconciles with."
    )


def test_start_md_f2_roster_scopes_the_split_claim_to_the_four_files() -> None:
    """D-019: the roster claimed a split that two members do not carry."""
    text = _read(START_MD)
    assert "each stream agent's own `## Rules` block" not in text, (
        "start.md's F2 roster still claims the split is in EVERY stream agent's "
        "own Rules block. GI-001 names four files; spec-test-deriver.md has no "
        "Rules block at all and coverage-diff.md's does not restate the split."
    )
    for named in (
        "`agents/assayer.md`",
        "`agents/tracer.md`",
        "`agents/flow-tracer.md`",
        "`agents/research-auditor.md`",
    ):
        assert named in text, (
            f"start.md's F2 roster no longer names {named} as a file carrying "
            f"the split. The scoped claim has to name its scope."
        )
    assert "`agents/spec-test-deriver.md`" in text and "`agents/coverage-diff.md`" in text, (
        "start.md's F2 roster does not say how the two roster members WITHOUT "
        "the split in their own prose are bound by it."
    )


def test_start_md_names_the_cycle_advancing_phase_token() -> None:
    """D-005 / ST-001 / AC-008: the F3 exit is the cycle-counter boundary."""
    text = _read(START_MD)
    assert "inspect_start" in text, (
        "start.md never names the `inspect_start` token. It is the F3 -> F2 "
        "exit that advances the server-side cycle counter, and the sibling F2 "
        "exit names its own tokens explicitly. The doc is the protocol of record."
    )
    assert "Foundry-Phase(phase='inspect_start')" in text, (
        "start.md names the token but not the call that emits it."
    )
    assert "The server derives the cycle; you never supply one." in text, (
        "start.md does not state that the cycle is server-derived (ST-001), so "
        "a lead may still pass one."
    )


def test_start_md_documents_the_liveness_tool() -> None:
    """D-020 / FR-015 / AC-021: a working tool with no documented caller."""
    text = _read(START_MD)
    assert "## TEAMMATE LIVENESS" in text, (
        "start.md has no liveness section. Foundry-Liveness is a shipped tool "
        "that no protocol prose told the lead to call."
    )
    assert "`Foundry-Liveness`" in text, "start.md never names the Foundry-Liveness tool"
    # D-052 / D-056: derived from the shipped enum, never a re-typed list. The
    # four statuses this loop used to name as literals stayed green while the
    # tool grew `done` and `no_ledger`, so the doc documented two-thirds of a
    # closed vocabulary and the pin had nothing to say about it. Reading
    # PROGRESS_STATUSES makes the next status fail here instead of drifting.
    for status in sorted(fs.PROGRESS_STATUSES):
        assert f"| `{status}` |" in text, (
            f"start.md's liveness table has no row for the `{status}` status. "
            f"The table is the lead's decision surface and the tool can return "
            f"every member of PROGRESS_STATUSES ({sorted(fs.PROGRESS_STATUSES)}); "
            f"an undocumented status is one the lead has no instruction for."
        )
    assert "needs_attention" in text, (
        "start.md does not mention the needs_attention array the tool returns."
    )
    assert "diagnostic and never a gate" in text, (
        "start.md does not state that liveness never gates the run, which is "
        "how a diagnostic turns into an unplanned halt."
    )


@pytest.mark.parametrize("path", (START_MD, RESUME_MD), ids=lambda p: p.name)
def test_command_allow_lists_permit_the_archive_migration(path: Path) -> None:
    """D-035 / FR-021 / AC-026: migrate-archive.py had no allow-list entry."""
    text = _read(path)
    assert "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/migrate-archive.py:*)" in text, (
        f"{_rel(path)} does not allow-list migrate-archive.py. The script is "
        f"shipped but unrunnable from the command that needs it."
    )


def test_resume_md_tells_the_lead_to_migrate_an_old_archive() -> None:
    """D-035: nothing told a run to invoke the migration it ships."""
    text = _read(RESUME_MD)
    assert "migrate-archive.py" in text, (
        "resume.md never mentions migrate-archive.py, so a pre-4.9 archive is "
        "resumed into with no migration and the tools read absent structures."
    )
    assert "${CLAUDE_PLUGIN_ROOT}/scripts/migrate-archive.py" in text, (
        "resume.md must reference the script through ${CLAUDE_PLUGIN_ROOT} -- "
        "the plugin cache is version-namespaced, so an absolute path rots."
    )
    assert "before" in text and "Foundry-Init" in text, (
        "resume.md does not order the migration before Foundry-Init. Migrating "
        "after the reload leaves the loaded state stale."
    )
    assert "idempotent" in text, (
        "resume.md does not say the migration is idempotent, so a lead will try "
        "to judge the archive's age by eye instead of just running it."
    )


# ---------------------------------------------------------------------------
# GRIND cycle 2 -- D-040, D-041, D-042
# ---------------------------------------------------------------------------

# The agent files that mandate a cite but that no casting owned in CAST. Their
# `file:line` mandates outlived the GRIND-1 conversion of the four stream
# agents, so FR-004 held on the verifiers and leaked everywhere else.
COVERAGE_DIFF = AGENTS / "coverage-diff.md"
PATTERN_MAPPER = AGENTS / "pattern-mapper.md"
CODEBASE_MAPPER = AGENTS / "codebase-mapper.md"
RESEARCHER = AGENTS / "researcher.md"
NYQUIST_AUDITOR = AGENTS / "nyquist-auditor.md"

PROVE_SKILL = SKILLS / "prove" / "SKILL.md"
TRACE_SKILL = SKILLS / "trace" / "SKILL.md"

# D-040 splits these agent files by ARTIFACT LIFETIME, not by file type. An
# artifact that is re-read while the tree moves under it must cite by symbol;
# one frozen against the tree it was written against may carry a line hint.
# Both halves are pinned, because converting everything is as wrong as
# converting nothing -- it would strip the locator off a verbatim excerpt.
SYMBOL_ONLY_AGENTS = (COVERAGE_DIFF, CODEBASE_MAPPER, RESEARCHER)


def test_the_grind2_prose_files_all_exist() -> None:
    """Floor check: every assertion below is vacuous if the corpus is empty."""
    for path in (
        *SYMBOL_ONLY_AGENTS,
        PATTERN_MAPPER,
        NYQUIST_AUDITOR,
        PROVE_SKILL,
        TRACE_SKILL,
    ):
        assert path.is_file(), f"missing pinned prose file: {_rel(path)}"


@pytest.mark.parametrize("path", SYMBOL_ONLY_AGENTS, ids=lambda p: p.name)
def test_unowned_agents_mandate_the_symbol_cite_form(path: Path) -> None:
    """D-040 / FR-004: these files still mandated `file:line` after GRIND-1."""
    text = _read(path)
    assert "`path#Symbol`" in text, (
        f"{_rel(path)} does not mandate the `path#Symbol` cite form. FR-004 is "
        f"a repo-wide placement rule -- converting the four stream agents while "
        f"leaving their neighbours on `file:line` keeps the loop open on every "
        f"artifact those neighbours produce."
    )
    assert "file:line" not in text, (
        f"{_rel(path)} still mandates a `file:line` cite. The artifact this "
        f"agent writes is re-read while the tree moves under it, so a line "
        f"hint there rots into a false finding."
    )


@pytest.mark.parametrize("path", SYMBOL_ONLY_AGENTS, ids=lambda p: p.name)
def test_unowned_agents_state_the_symbol_is_authoritative(path: Path) -> None:
    """D-040: the validity rule, not merely the placement rule."""
    text = _flat(path)
    assert "symbol is authoritative" in text, (
        f"{_rel(path)} does not state that the symbol decides validity. Without "
        f"it a drifted line hint reads as a broken cite and the reader files a "
        f"finding for a line that merely moved."
    )
    assert "a moved line alone produces no finding of any kind" in text, (
        f"{_rel(path)} lost the no-finding-for-a-moved-line rule (FR-004)."
    )
    assert "cite-refresh sweep" in text, (
        f"{_rel(path)} no longer prohibits unprompted cite-refresh sweeps -- a "
        f"sweep manufactures churn across the whole tree."
    )
    assert "commit-pinned run artifact" in text, (
        f"{_rel(path)} does not say where a line hint IS still permitted, so "
        f"the placement rule reads as a blanket ban."
    )


@pytest.mark.parametrize("path", SYMBOL_ONLY_AGENTS, ids=lambda p: p.name)
def test_unowned_agent_examples_carry_no_line_cites(path: Path) -> None:
    """D-040's D-016 half: a worked example is what a reader actually copies."""
    stale = _LINE_CITE_RE.findall(_read(path))
    assert not stale, (
        f"{_rel(path)} still shows line-numbered cite(s) {stale} in a worked "
        f"example. Prose mandating `path#Symbol` beside an example emitting "
        f"`path:line` is a contradiction, and the example wins."
    )


def test_coverage_diff_rules_the_carve_out_against_itself() -> None:
    """D-040: coverage-diff is a live F2 stream feeding Foundry-Sync."""
    text = _read(COVERAGE_DIFF)
    assert "carve-out that permits a line hint does not reach" in text, (
        "coverage-diff.md does not rule on whether its own findings record "
        "qualifies for the commit-pinned run-artifact carve-out. It does not: "
        "the record flows into Foundry-Sync and is re-read every GRIND cycle. "
        "Leaving the ruling unstated is what let mandate and examples drift."
    )
    assert "Foundry-Sync" in text, (
        "coverage-diff.md no longer names the sync path that makes its record "
        "long-lived, which is the whole reason the carve-out does not apply."
    )


def test_coverage_diff_keeps_the_manifest_entry_format_intact() -> None:
    """D-040 boundary: `source_file:symbol` is the manifest's own spelling.

    D-073 corrected this docstring, which repeated the file's error. The prose
    used to say the shape is one "which the manifest validator requires
    verbatim", and this docstring said ``foundry_validate`` "requires every
    coverage_list entry to be shaped path/to/file.go:TestSymbolName". Neither
    is true: Dimension 8's only per-entry check is ``isinstance(entry, str)``
    (the shape appears in an issue DETAIL string, never in a condition), so a
    bare ``"foo"`` validates. ``test_the_coverage_entry_shape_is_unenforced``
    below drives that and is what keeps the claim honest.

    The colon argument survives intact and on its own merits -- that colon
    separates a path from a SYMBOL, never from a line, so the FR-004 placement
    rule has no quarrel with it and "convert every colon" would still have
    broken the manifest's spelling. What is gone is the appeal to enforcement.
    """
    text = _read(COVERAGE_DIFF)
    assert "`source_file:symbol`" in text, (
        "coverage-diff.md no longer documents the `source_file:symbol` shape "
        "a coverage_list entry is spelled in."
    )
    assert "never from a line" in text, (
        "coverage-diff.md does not explain why its `source_entry` colon is not "
        "a line hint. Without that, the next FR-004 sweep 'fixes' a validated "
        "input format and the migration stream stops parsing its own manifest."
    )


def test_coverage_diff_abolishes_its_severity_tier() -> None:
    """D-041's axis, in a file D-040 opened: orphans were 'low severity'."""
    text = _read(COVERAGE_DIFF)
    assert "low severity" not in text, (
        "coverage-diff.md still tiers a finding as 'low severity'. Every "
        "stream agent's Rules block abolishes the severity axis; a live F2 "
        "stream keeping one is the same contradiction D-041 was filed for."
    )
    assert "**No severity classification.**" in text, (
        "coverage-diff.md has no rule abolishing severity, unlike its four "
        "peer stream agents. The channel decides where a finding goes; a tier "
        "decides nothing and only licenses skipping."
    )


def test_pattern_mapper_rules_its_carve_out_explicitly() -> None:
    """D-040: PATTERNS.md is the one artifact where a line range is correct.

    The lead's GRIND-1 concern was that converting start.md's description
    without its producer would make command prose disagree with the agent that
    writes the artifact. The resolution is a stated ruling on both sides, not a
    silent conversion of one.
    """
    text = _flat(PATTERN_MAPPER)
    assert "commit-pinned run artifact" in text, (
        "pattern-mapper.md keeps `file:line` cites but never says under which "
        "rule they are legitimate. An unexplained exception reads as an "
        "oversight and the next FR-004 sweep deletes it."
    )
    assert "the body is the payload" in text, (
        "pattern-mapper.md does not say WHY the range is safe here -- because "
        "the excerpt body travels with the cite and is what gets mirrored."
    )
    assert "A drifted range is still never a finding." in text, (
        "pattern-mapper.md's carve-out does not restate the no-finding rule. "
        "The carve-out permits WRITING a line hint; it never licenses a "
        "verifier to JUDGE one, and conflating those reopens the loop."
    )


def test_start_md_spot_check_greps_the_body_not_the_line() -> None:
    """D-040: the F0.9 check is the consumer of pattern-mapper's ruling."""
    text = _flat(START_MD)
    assert "grepping **the excerpt's own text** in the cited file" in text, (
        "start.md's Dimension 11d still verifies an excerpt by grepping the "
        "cited file:line. That makes a moved line an F0.9 ERROR -- a verifier "
        "judging the line component, which FR-004 forbids outright."
    )
    assert "Never make the line range the verdict" in text, (
        "start.md does not state that the range is a locator rather than the "
        "verdict, so the check and pattern-mapper.md's ruling still disagree."
    )


# ---------------------------------------------------------------------------
# D-041 -- the skills' normative JSON schemas
# ---------------------------------------------------------------------------

# GRIND-1 converted these two skills' PROSE. Their machine-readable schemas
# kept a required severity enum and a line field -- in documents that say their
# output "can be passed directly to the foundry defect sync tools". A schema
# outranks the prose beside it, so the abolished axis was still normative.
SCHEMA_BEARING_SKILLS = (PROVE_SKILL, TRACE_SKILL)


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_schemas_carry_no_severity_axis(path: Path) -> None:
    """D-041: the reconciled vocabulary has no severity."""
    text = _read(path)
    assert '"severity"' not in text, (
        f"{_rel(path)}'s findings schema still declares a `severity` property. "
        f"Every agent file abolishes the axis; a normative schema that "
        f"re-introduces it hands every stream a tier to skip work with."
    )
    assert '"by_severity"' not in text, (
        f"{_rel(path)}'s summary block still rolls findings up by severity."
    )
    assert '"by_classification"' in text, (
        f"{_rel(path)} does not roll findings up by classification. DEFECT vs "
        f"OBSERVATION is the axis that replaced severity, and the summary has "
        f"to count on the axis the records actually carry."
    )
    assert "adding one is a vocabulary violation" in text, (
        f"{_rel(path)} removed the severity field but never says it must not "
        f"come back. A silently-absent field gets re-added by the next author "
        f"who misses it; a stated prohibition does not."
    )


#: The property names in a skill's findings schema that are ALLOWED to carry a
#: closed enum, mapped to the vocab.py export each must equal. Any other
#: enum-bearing key is a graded axis smuggled back in under a new name -- a
#: `"priority": ["P0", "P1", "P2"]` property passes every substring check ever
#: written for `"severity"` while reinstating exactly the tier D-041 removed.
#: `verdict` is a roll-up of the findings, not a grade on any one of them, so
#: it is enumerated here rather than derived from vocab.
_ALLOWED_ENUM_KEYS = frozenset({"classification", "type", "verdict"})

#: The alias members of DEFECT_TYPES -- spellings that fold onto another
#: member. Derived from the module rather than re-typed, so adding a second
#: alias needs no edit here.
_DEFECT_TYPE_ALIASES = frozenset(
    t for t in vocab.DEFECT_TYPES if vocab.canonical_defect_type(t) != t
)

#: What a skill's `type` enum must equal: every DEFECT_TYPES member a stream
#: may put on the wire, with the alias spellings folded away. The skills
#: document MISPLACED in prose (it is accepted) but do not list it, because an
#: enum offering two spellings of one value invites streams to split on it.
_EXPECTED_TYPE_ENUM = frozenset(vocab.DEFECT_TYPES) - _DEFECT_TYPE_ALIASES


def _findings_schema(path: Path) -> dict:
    """Parse the single ```json findings schema out of a skill file.

    The skills say their output "can be passed directly to the foundry defect
    sync tools", which makes this block normative: it outranks the prose beside
    it. Parsing it (rather than grepping it) is what lets the assertions below
    compare SETS -- a substring check cannot tell a missing member from a
    present one, and cannot see a new key at all.
    """
    blocks = re.findall(r"```json\n(.*?)\n```", _read(path), re.S)
    assert len(blocks) == 1, (
        f"{_rel(path)} has {len(blocks)} ```json blocks, expected exactly 1. "
        f"These assertions target the findings schema; if the file gained a "
        f"second schema, select the right one rather than dropping the checks."
    )
    try:
        return json.loads(blocks[0])
    except json.JSONDecodeError as exc:  # pragma: no cover - fails loudly
        raise AssertionError(
            f"{_rel(path)}'s findings schema is not valid JSON ({exc}). The "
            f"file states it can be passed directly to the defect sync tools, "
            f"so a schema that does not parse is a broken contract."
        ) from exc


def _iter_enums(node: object, key: str | None = None) -> list[tuple[str, frozenset]]:
    """Every ``enum`` in a JSON-schema tree, paired with the property it sits on."""
    found: list[tuple[str, frozenset]] = []
    if isinstance(node, dict):
        if isinstance(node.get("enum"), list) and key is not None:
            found.append((key, frozenset(node["enum"])))
        for child_key, child in node.items():
            # Recurse under the child's own name, except through the JSON-Schema
            # keywords that merely wrap a subtree -- otherwise every property
            # would be reported as "properties" or "items".
            next_key = key if child_key in {"properties", "items"} else child_key
            found.extend(_iter_enums(child, next_key))
    elif isinstance(node, list):
        for child in node:
            found.extend(_iter_enums(child, key))
    return found


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_schemas_use_the_reconciled_vocabulary(path: Path) -> None:
    """D-048: the enum is compared against vocab.py, not against a re-typed tuple.

    The assertion this replaces was a CONTAINS check over five hard-coded
    strings. Four drift scenarios passed it unchanged: vocab.py gaining a
    member the skill never learns; the skill advertising a value the server
    rejects; the skill dropping a member outside the five; and the severity
    axis returning under a new key. All four now fail.
    """
    schema = _findings_schema(path)
    enums = dict(_iter_enums(schema))

    assert enums.get("classification") == frozenset(vocab.FINDING_CLASSES), (
        f"{_rel(path)}'s classification enum is {sorted(enums.get('classification') or [])}, "
        f"but vocab.FINDING_CLASSES is {sorted(vocab.FINDING_CLASSES)}. The channel "
        f"a finding goes down must be closed over exactly the FINDING_CLASSES "
        f"members -- no more (a third channel nothing reads) and no fewer (a "
        f"stream with nowhere to file comment prose files it as a defect)."
    )

    actual_types = enums.get("type")
    assert actual_types is not None, (
        f"{_rel(path)}'s findings schema has no `type` enum at all, so a stream "
        f"may put any string on the wire and have it refused at the boundary."
    )
    assert actual_types == _EXPECTED_TYPE_ENUM, {
        "file": _rel(path),
        "advertised_but_not_a_vocab_member": sorted(actual_types - frozenset(vocab.DEFECT_TYPES)),
        "vocab_member_the_skill_never_learned": sorted(_EXPECTED_TYPE_ENUM - actual_types),
        "why": (
            "The skill's type enum must equal vocab.DEFECT_TYPES minus the "
            "alias spellings. A value the skill advertises but vocab rejects "
            "is dropped at the MCP boundary before it reaches the ledger "
            "(this is D-045's failure mode); a vocab member the skill omits "
            "is a verdict the stream has no legal way to file."
        ),
    }

    text = _read(path)
    assert "ARCHITECTURAL_PLACEMENT" in text and "MISPLACED" in text, (
        f"{_rel(path)} does not record that MISPLACED folds onto "
        f"ARCHITECTURAL_PLACEMENT. Both spellings are live in agent contracts."
    )
    assert "schemas/vocab.py#DEFECT_TYPES" in text, (
        f"{_rel(path)} does not cite the vocabulary module as the source of "
        f"truth, so this enum becomes a seventh copy free to drift."
    )


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_schemas_carry_no_enum_outside_the_allowed_axes(path: Path) -> None:
    """D-048: the positive half -- no key other than the allowed three grades.

    ``test_skill_schemas_carry_no_severity_axis`` greps for the literal
    ``"severity"``. That catches the field coming back under its old name and
    nothing else. This catches it coming back under ANY name, which is the
    form a future author is far likelier to reach for.
    """
    schema = _findings_schema(path)
    unexpected = {
        key: sorted(members)
        for key, members in _iter_enums(schema)
        if key not in _ALLOWED_ENUM_KEYS
    }
    assert not unexpected, (
        f"{_rel(path)}'s findings schema declares enum-bearing propert(ies) "
        f"{unexpected} outside the allowed axes {sorted(_ALLOWED_ENUM_KEYS)}. "
        f"Every defect gets fixed, so a graded axis has nothing left to "
        f"decide -- and a tier reintroduced under a new key ('priority', "
        f"'impact', 'tier') is the same abolished axis wearing a different "
        f"name. If a genuinely new closed vocabulary is needed, add it to "
        f"schemas/vocab.py first and widen _ALLOWED_ENUM_KEYS deliberately."
    )


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_schemas_require_the_new_axes(path: Path) -> None:
    """D-041: an optional classification is a classification streams omit."""
    text = _read(path)
    assert '"required": ["id", "classification", "type", "file", "symbol", "description"]' in text, (
        f"{_rel(path)}'s required list does not demand classification, type "
        f"and symbol. `severity` was REQUIRED before this fix -- replacing a "
        f"required field with optional ones weakens the contract instead of "
        f"correcting it."
    )


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_schemas_drop_the_line_field_for_a_symbol(path: Path) -> None:
    """D-041: align the line field with the FR-004 placement rule."""
    text = _read(path)
    assert '"line": {"type": "integer"' not in text, (
        f"{_rel(path)}'s findings schema still declares a `line` property. The "
        f"file's own prose mandates `path#Symbol`; a schema slot for a line "
        f"number tells a stream to do the opposite of what the prose requires."
    )
    assert '"symbol": {"type": "string"' in text, (
        f"{_rel(path)}'s schema has no `symbol` property, so a stream told to "
        f"cite `path#Symbol` has nowhere to put the half that is authoritative."
    )
    assert "carve-out that permits a line hint does not reach a findings record" in text, (
        f"{_rel(path)} does not rule on whether its findings record qualifies "
        f"for the commit-pinned run-artifact carve-out. It does not -- the "
        f"record is passed to the defect sync tools and re-read for cycles."
    )


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_verdict_rules_key_on_classification(path: Path) -> None:
    """D-041: the verdict was computed on the axis being abolished."""
    text = _flat(path)
    assert "**FAIL**: any finding classified `DEFECT`" in text, (
        f"{_rel(path)}'s verdict rules still tier findings before deciding "
        f"FAIL. Removing the schema field while the verdict rule keeps "
        f"grading by importance leaves the severity axis fully operative."
    )
    assert "**WARN**: findings exist but every one is classified `OBSERVATION`" in text, (
        f"{_rel(path)}'s WARN rule does not key on the observation channel."
    )


def test_prove_skill_closed_the_critical_path_exemption() -> None:
    """D-041: 'not on the critical path' was severity under another name."""
    text = _flat(PROVE_SKILL)
    assert "there is no off-the-critical-path exemption" in text, (
        "prove/SKILL.md's verdict rules downgraded a non-VERIFIED item to WARN "
        "when it sat off the critical path. assayer.md's 'EVERY non-VERIFIED "
        "verdict is a defect, no exceptions' admits no such exemption, and an "
        "importance test by any other name is the axis D-041 removes."
    )


def test_trace_skill_states_plumber_findings_are_defects_not_a_tier() -> None:
    """D-041: the prose twin of the schema's severity enum."""
    text = _flat(TRACE_SKILL)
    assert "high severity" not in text, (
        "trace/SKILL.md still calls PL-N findings 'high severity'. Fixing the "
        "schema while the prose two screens up asserts a tier leaves the file "
        "contradicting itself, and a reader resolves that in its own favour."
    )
    assert "PL-N findings are defects, never observations" in text, (
        "trace/SKILL.md no longer states the CHANNEL a broken-workflow finding "
        "goes down. Deleting the severity claim without replacing it loses the "
        "point the sentence was making."
    )


# ---------------------------------------------------------------------------
# D-042 -- pathspec commits everywhere an agent commits
# ---------------------------------------------------------------------------


def test_nyquist_auditor_commits_with_a_pathspec() -> None:
    """D-042 / FR-011 / CT-005: it mandated the bare form teammate.md forbids."""
    text = _read(NYQUIST_AUDITOR)
    assert 'git commit -m "test(nyquist): regression cover for {req-id}" -- <test-file>' in text, (
        "nyquist-auditor.md's Step 7 does not commit with an explicit "
        "pathspec. F5.5 shares one index with every other agent, so the bare "
        "form captures whatever a peer has staged -- the exact failure "
        "teammate.md's COMMIT PROTOCOL exists to prevent."
    )
    assert "NEVER run a bare `git commit -m" in text, (
        "nyquist-auditor.md shows the pathspec form but never forbids the bare "
        "one. An example without a prohibition is a suggestion."
    )
    assert "commits the ENTIRE index by git's documented default" in text, (
        "nyquist-auditor.md does not say WHY a bare commit is unsafe. D-042's "
        "failure mode was an agent following a form it did not understand."
    )


def test_nyquist_auditor_codifies_no_stash_and_no_bypass() -> None:
    """AC-017: the no-stash rule and the absence of any bypass path."""
    text = _read(NYQUIST_AUDITOR)
    assert "Never run `git stash`, in any form." in text, (
        "nyquist-auditor.md does not codify the no-stash rule. `git stash "
        "--keep-index` silently drops the unstaged half of a partially-staged "
        "file and takes every peer's uncommitted work with it."
    )
    assert HOOK_BYPASS_FLAG not in text, (
        f"nyquist-auditor.md offers `{HOOK_BYPASS_FLAG}` somewhere. No protocol "
        f"path may hand an agent a hook bypass: the shipped guard judges staged "
        f"content only, so a correct pathspec commit already passes it."
    )
    assert "judges staged content only" in text, (
        "nyquist-auditor.md does not explain that the guard reads the index "
        "rather than the tree, which is the fact that makes bypassing it "
        "unnecessary rather than merely forbidden."
    )


def test_start_md_evidence_strip_is_pathspec_scoped() -> None:
    """FR-011 / CT-005: the F6 evidence-strip step was a bare commit too."""
    text = _read(START_MD)
    assert (
        'git commit -m "chore(foundry): strip consumed run evidence" -- evidence/'
        in text
    ), (
        "start.md's F6 evidence-strip step still runs a bare `git commit`. It "
        "is the last commit of the run and it takes the whole shared index, so "
        "anything still staged ships under a 'strip evidence' message."
    )
    assert "matches staged deletions the same way" in text, (
        "start.md does not state that a pathspec still commits the `git rm` "
        "deletions. Without that, a lead reads the pathspec as a risk to the "
        "removal and drops it."
    )


# ---------------------------------------------------------------------------
# GRIND cycle 3 -- D-045, D-046, D-051: the hand-maintained-list class
# ---------------------------------------------------------------------------
#
# D-046 and D-051 are the same miss twice: a file that needed the FR-004 cite
# treatment was absent from a hand-written tuple, so converting its peers
# reported success while it kept its line cites. The tuples above are the
# lists that were wrong. The two sweeps below are DERIVED from the directory,
# so a file cannot be missing from them -- a new agent is covered the moment
# it lands, without anyone remembering to add it.

#: The one agent file permitted to carry `file:line` cites, and the reason.
#: PATTERNS.md is a commit-pinned run artifact: pattern-mapper writes it at
#: F0.6 against one tree state and F0.5 pastes its excerpts verbatim into
#: casting prompts before any casting has committed a line. The line range is
#: the LOCATOR for an excerpt whose body travels with it, and the body is what
#: a teammate mirrors -- so the range is verified by matching the quoted body,
#: never by reading the number. pattern-mapper.md rules on this explicitly in
#: its own Critical rules block; this exclusion mirrors that ruling rather
#: than inventing a second one.
LINE_CITE_EXEMPT_AGENTS = frozenset({"pattern-mapper.md"})


def test_the_line_cite_exemption_still_documents_itself() -> None:
    """An exemption nobody justifies is an exemption nobody can review."""
    text = _read(PATTERN_MAPPER)
    assert "PATTERNS.md is a commit-pinned run artifact" in text, (
        "pattern-mapper.md no longer justifies its own line-cite exemption. "
        "LINE_CITE_EXEMPT_AGENTS points at that ruling; if the ruling is gone "
        "the exemption has no basis and the file should be swept like every "
        "other agent."
    )
    assert "a drifted range is still never a finding" in text.lower(), (
        "pattern-mapper.md lost the rule that its own drifted ranges never "
        "produce a finding. Without it the exemption becomes a licence to "
        "file exactly the findings FR-004 abolished."
    )


def test_no_unexempted_agent_file_carries_a_line_cite() -> None:
    """D-051: intent-carrier.md was in NO tuple, so its cite rotted unseen.

    ``agents/intent-carrier.md`` cited a Forge finalization rule by line range.
    The range had ALREADY drifted -- it pointed at a markdown coverage table,
    not at the rule -- making it a live instance of the exact failure FR-004
    exists to prevent, inside the plugin that ships the rule. Nothing caught
    it because every line-cite assertion in this module ran over a hand-listed
    tuple that did not include the file.

    This sweep is derived from the directory. It cannot omit a file.
    """
    offenders = {
        path.name: _LINE_CITE_RE.findall(_read(path))
        for path in sorted(AGENTS.glob("*.md"))
        if path.name not in LINE_CITE_EXEMPT_AGENTS
        and _LINE_CITE_RE.search(_read(path))
    }
    assert not offenders, (
        f"agent file(s) carry line-numbered cite(s): {offenders}. A committed "
        f"agent .md is not a commit-pinned run artifact -- it is re-read for "
        f"the life of the plugin while the tree moves under it, so a line hint "
        f"there rots into a false finding. Cite `path#Symbol` instead. If a "
        f"file genuinely qualifies for the run-artifact carve-out, add it to "
        f"LINE_CITE_EXEMPT_AGENTS *with* a stated justification in the file."
    )


def test_the_agent_sweep_actually_reads_files() -> None:
    """Floor check: a glob that matches nothing asserts nothing."""
    swept = [p for p in AGENTS.glob("*.md") if p.name not in LINE_CITE_EXEMPT_AGENTS]
    assert len(swept) >= 10, (
        f"the agent line-cite sweep found only {len(swept)} files. It is "
        f"supposed to cover the whole agents/ directory; if the layout moved, "
        f"repoint the glob rather than letting the assertion go vacuous."
    )
    assert any(p.name == "intent-carrier.md" for p in swept), (
        "intent-carrier.md is not in the swept set -- it is the file D-051 was "
        "filed against, so it must be covered."
    )


# A `source:` value being handed to Foundry-Defect in agent/skill/command
# prose. Anchored on the key so prose merely DISCUSSING the vocabulary is not
# a hit; the value is captured for membership testing.
_WIRE_SOURCE_RE = re.compile(r"source:\s*`?\"([a-z0-9_-]+)\"")

# Every markdown surface that instructs an agent to file a defect.
_DEFECT_FILING_PROSE = (
    sorted(AGENTS.glob("*.md"))
    + sorted(FOUNDRY_ROOT.glob("skills/*/SKILL.md"))
    + sorted(COMMANDS.glob("*.md"))
)


def test_every_instructed_defect_source_is_a_vocab_member() -> None:
    """D-045: temper/SKILL.md instructed a `source` the server hard-refuses.

    ``skills/temper/SKILL.md`` told its sweep pass to file findings with
    ``source: "temper-sweep"``. That value is not a DEFECT_SOURCE_IDS member,
    so it was refused twice over -- once by the MCP input schema, which builds
    its enum from ``sorted(DEFECT_SOURCE_IDS)``, and once server-side. Every
    finding a TEMPER sweep filed as instructed was dropped before reaching the
    ledger, and the same file used the legal ``"temper"`` two screens earlier,
    so it was one drifted spelling rather than a design disagreement.

    Nothing connected the markdown to the vocabulary, so nothing failed. This
    is that connection, and it is derived over every file that instructs a
    filing rather than over a list someone has to maintain.
    """
    illegal: dict[str, list[str]] = {}
    for path in _DEFECT_FILING_PROSE:
        bad = sorted(
            {
                value
                for value in _WIRE_SOURCE_RE.findall(_read(path))
                if value not in vocab.DEFECT_SOURCE_IDS
            }
        )
        if bad:
            illegal[_rel(path)] = bad
    assert not illegal, (
        f"prose instructs Foundry-Defect `source` value(s) the server refuses: "
        f"{illegal}. Legal values are {sorted(vocab.DEFECT_SOURCE_IDS)}. A "
        f"refused source is not a soft failure -- the finding never reaches "
        f"the ledger, so the stream reports work it did not persist. Fix the "
        f"spelling in the prose; do not widen the vocabulary to match a typo."
    )


def test_the_source_sweep_finds_the_known_call_sites() -> None:
    """Floor check: the regex must actually match the prose it guards."""
    found = {
        value
        for path in _DEFECT_FILING_PROSE
        for value in _WIRE_SOURCE_RE.findall(_read(path))
    }
    assert "temper" in found, (
        "the `source:` sweep matched no `temper` instruction. skills/temper/"
        "SKILL.md routes its findings that way, so a miss means the regex no "
        "longer matches the prose shape and the guard has gone vacuous."
    )


# ---------------------------------------------------------------------------
# GRIND cycle 4 -- D-052/D-056 (liveness table lied), D-053/D-055 (write half)
# ---------------------------------------------------------------------------

# The two membership lines in start.md's liveness section. Split into a label
# and a payload so the assertion reads the payload only: the label itself
# contains the words `needs_attention`, and a regex over the whole line would
# count that as a status being named.
_IN_NEEDS_ATTENTION_LABEL = "- **In `needs_attention`:**"
_NOT_IN_NEEDS_ATTENTION_LABEL = "- **Not in `needs_attention`:**"

# A markdown code span holding a lowercase identifier -- the shape every
# liveness status is written in.
_CODE_SPAN_RE = re.compile(r"`([a-z_]+)`")


def _statuses_named_after(label: str) -> set[str]:
    """Return the liveness statuses named on the start.md line with ``label``.

    Filtered to PROGRESS_STATUSES members so an unrelated code span in the
    same sentence is not read as a status -- and so a MISSPELLED status is
    dropped rather than accepted, which turns the set comparison red exactly
    the way a wrong status should.
    """
    for line in _read(START_MD).splitlines():
        if line.startswith(label):
            payload = line[len(label) :]
            return {
                token
                for token in _CODE_SPAN_RE.findall(payload)
                if token in fs.PROGRESS_STATUSES
            }
    raise AssertionError(
        f"start.md has no line beginning '{label}'. The needs_attention "
        f"membership must be stated as an explicit list on its own line -- a "
        f"prose RULE ('every agent whose status is not progressing') is what "
        f"D-052/D-056 found already false, and no test can check a rule."
    )


def test_start_md_states_needs_attention_membership_as_the_tool_computes_it() -> None:
    """D-052 / D-056: the doc asserted a rule the shipped tool contradicts.

    start.md said the array holds "every agent whose status is not
    `progressing`". ``foundry_liveness`` builds it from
    NEEDS_ATTENTION_STATUSES, which excludes `done` as well -- deliberately,
    because a finished agent on the watchlist is the silting-up the terminal
    line exists to stop. Under the doc's rule every completed casting would
    live in needs_attention forever.

    Both directions are pinned by set equality against the real frozensets, so
    a status added to either side of the enum, or moved between them, fails
    here instead of quietly making the doc wrong again.
    """
    listed = _statuses_named_after(_IN_NEEDS_ATTENTION_LABEL)
    assert listed == set(fs.NEEDS_ATTENTION_STATUSES), (
        f"start.md lists {sorted(listed)} as the needs_attention members; the "
        f"tool returns {sorted(fs.NEEDS_ATTENTION_STATUSES)}. The array is a "
        f"call-to-action list -- a doc that overstates it trains the lead to "
        f"ignore it, and one that understates it hides a wedged agent."
    )

    excluded = _statuses_named_after(_NOT_IN_NEEDS_ATTENTION_LABEL)
    expected_excluded = set(fs.PROGRESS_STATUSES) - set(fs.NEEDS_ATTENTION_STATUSES)
    assert excluded == expected_excluded, (
        f"start.md names {sorted(excluded)} as excluded from needs_attention; "
        f"the tool excludes {sorted(expected_excluded)}. Stating the exclusion "
        f"explicitly is what makes `done` legible as a deliberate omission "
        f"rather than an oversight the next reader 'fixes'."
    )

    # Belt and braces: the two lists must partition the vocabulary, so no
    # status can be added to the table and then omitted from both lines.
    assert listed | excluded == set(fs.PROGRESS_STATUSES), (
        f"the two membership lines cover {sorted(listed | excluded)} but the "
        f"vocabulary is {sorted(fs.PROGRESS_STATUSES)}. Every status must be "
        f"on exactly one of the two lines."
    )
    assert not (listed & excluded), (
        f"{sorted(listed & excluded)} appears on both membership lines."
    )


def test_start_md_liveness_table_explains_the_terminal_status() -> None:
    """D-052: `done` is not just a sixth row, it is the row with a reason.

    D-067 narrowed what this may assert. The original second assertion was the
    bare literal ``"outranks every age check"``, written when the precedence
    WAS unconditional. 9c1e69d then made it conditional (``superseded``), and
    the bare literal went on defending the stale sentence -- a fixer rewriting
    the row to state the real rule broke a green test and was pushed back
    toward the wrong wording. A pin that outlives its behaviour is worse than
    no pin, so the precedence is now asserted TOGETHER with its exception in
    ``test_start_md_done_row_states_the_supersede_exception`` below, and this
    test keeps only the part that is still unconditionally true: the row names
    the ledger field that produces the status.
    """
    text = _flat(START_MD)
    assert f'`"{fs.TERMINAL_FIELD}": true`' in text, (
        "start.md's liveness table documents the `done` status without naming "
        "the ledger field that produces it, so a lead reading a `done` row "
        "cannot tell what the agent actually did to earn it."
    )


# ---------------------------------------------------------------------------
# FR-015 write half -- D-053 / D-055
#
# The ledger filename is the stream's WIRE id, never its agent filename:
# `foundry_liveness` globs `progress/*.jsonl` and takes `path.stem` as the
# agent, and `_missing_stream_records` synthesizes its no_ledger row keyed on
# the same wire id. An assayer writing `assayer.jsonl` would therefore appear
# TWICE -- once under a stem nothing expects, and once as a permanent
# `no_ledger` for `prove`. That trap is why the mapping is asserted against
# the roster rather than eyeballed.
# ---------------------------------------------------------------------------

STREAM_LEDGER_IDS = {
    ASSAYER: "prove",
    TRACER: "trace",
    FLOW_TRACER: "flow_trace",
    RESEARCH_AUDITOR: "research_audit",
}


def test_the_stream_ledger_ids_are_the_liveness_roster() -> None:
    """Floor check: the mapping below must BE the roster the tool expects."""
    assert set(STREAM_LEDGER_IDS.values()) == set(fs.INSPECT_STREAM_AGENT_IDS), (
        f"this module maps the stream agents to {sorted(STREAM_LEDGER_IDS.values())}; "
        f"foundry_spawn expects ledgers from {sorted(fs.INSPECT_STREAM_AGENT_IDS)}. "
        f"A stream added to the roster needs the protocol in its agent file, and "
        f"this mapping is what makes that omission fail."
    )
    assert set(STREAM_LEDGER_IDS) == set(STREAM_AGENTS), (
        "the ledger mapping and the split parametrisation disagree about which "
        "files are the four defect-filing streams."
    )


@pytest.mark.parametrize(
    ("path", "wire_id"), sorted(STREAM_LEDGER_IDS.items()), ids=lambda v: getattr(v, "name", v)
)
def test_each_stream_agent_carries_the_progress_protocol(path: Path, wire_id: str) -> None:
    """D-053 / D-055 / FR-015 / GI-001: the write half, baked into the files.

    FR-015 requires spawned agents to append progress lines, "protocol
    instruction in agent prompts". For a teammate that instruction rides on the
    spawn prompt ``foundry_spawn`` builds. The four F2 streams are spawned from
    the roster in commands/start.md instead, so nothing hands them anything --
    and GRIND-3's ``no_ledger`` status only made the silence VISIBLE. This is
    the delivery: the same durable placement GI-001 uses for the
    observation/defect split, in the agent file itself, on a fresh checkout.
    """
    text = _read(path)
    flat = _flat(path)

    # Anchored on the HEADING, not the string: the Rules bullet below refers to
    # "`## Progress ledger` section" in backticks, so a bare substring check
    # stays green when the section itself is deleted and only the pointer
    # survives -- verified by mutation, and exactly the vacuous-pin shape D-056
    # filed against the old liveness test.
    assert "\n## Progress ledger\n" in text, (
        f"{_rel(path)} has no `## Progress ledger` section heading. Without the "
        f"section this stream writes no ledger and is invisible to "
        f"Foundry-Liveness for the whole of F2 -- the exact gap D-053/D-055 "
        f"filed. (A Rules bullet POINTING at the section is not the section.)"
    )
    assert "`## Progress ledger` section" in text, (
        f"{_rel(path)}'s `## Rules` block does not bind the ledger obligation. "
        f"The Rules block is this file's normative register; a section nothing "
        f"in Rules points at reads as background."
    )


@pytest.mark.parametrize(
    ("path", "wire_id"), sorted(STREAM_LEDGER_IDS.items()), ids=lambda v: getattr(v, "name", v)
)
def test_stream_ledger_path_matches_what_liveness_reads(path: Path, wire_id: str) -> None:
    """D-053: the path is a contract with `_read_progress_ledger`, not a hint.

    Assembled from the shipped constants rather than typed out, so moving the
    directory or renaming a stream turns this red instead of leaving four
    agent files writing where nothing looks.
    """
    expected = f"foundry-archive/{{run}}/{fs.PROGRESS_DIR_NAME}/{wire_id}.jsonl"
    text = _read(path)
    assert expected in text, (
        f"{_rel(path)} does not name its ledger as `{expected}`. "
        f"foundry_liveness globs {fs.PROGRESS_DIR_NAME}/*.jsonl and takes the "
        f"filename stem as the agent id, so a ledger written anywhere else -- "
        f"or under this agent's FILE name instead of its wire id `{wire_id}` -- "
        f"is reported under an id nothing expects while `{wire_id}` stays "
        f"permanently `no_ledger`."
    )
    assert "not for this agent file" in _flat(path), (
        f"{_rel(path)} does not warn that the ledger is named for the wire id "
        f"`{wire_id}` rather than the agent file. That confusion produces two "
        f"wrong rows in one call, and it is the likeliest way to get this wrong."
    )


@pytest.mark.parametrize(
    ("path", "wire_id"), sorted(STREAM_LEDGER_IDS.items()), ids=lambda v: getattr(v, "name", v)
)
def test_stream_ledger_line_shape_matches_what_liveness_parses(
    path: Path, wire_id: str
) -> None:
    """D-053 / D-055: the three fields and the terminal line, byte-for-byte.

    ``_read_progress_ledger`` keeps a line only if it parses as a JSON object
    with a ``timestamp`` ``_parse_progress_timestamp`` accepts; ``_step_key``
    reads ``phase`` and ``step``; ``_is_terminal`` requires ``done`` to be
    exactly ``true``. Prose that names different fields produces a ledger the
    tool silently discards -- an agent dutifully writing lines and still
    reported `unknown`.
    """
    text = _read(path)
    flat = _flat(path)

    for field in ('"timestamp"', '"phase"', '"step"'):
        assert field in text, (
            f"{_rel(path)}'s progress protocol never names the {field} field. "
            f"A line missing it is dropped by _read_progress_ledger or read as "
            f"an empty step key, so the agent writes and is still invisible."
        )
    assert '"phase": "inspect"' in text, (
        f"{_rel(path)} does not pin `phase` to `inspect`. The synthesized "
        f"no_ledger row for this stream reports phase `inspect`; a stream that "
        f"writes something else makes the two rows disagree about one agent."
    )
    assert f'"{fs.TERMINAL_FIELD}": true' in text, (
        f"{_rel(path)} never tells this stream to write the terminal "
        f'`"{fs.TERMINAL_FIELD}": true` line. Without it the stream finishes, '
        f"stops writing, crosses the threshold and reports `{fs.STATUS_STALLED}` "
        f"for the rest of the run -- refilling needs_attention with completed "
        f"work, which is D-022 all over again."
    )

    cadence_min = fs.PROGRESS_CADENCE_SECONDS // 60
    stall_min = fs.STALL_THRESHOLD_SECONDS // 60
    assert f"{cadence_min} minutes" in flat, (
        f"{_rel(path)} does not state the {cadence_min}-minute cadence. The "
        f"number is derived from real spawn timings (FR-025); prose that omits "
        f"it leaves the agent guessing how often 'periodic' is."
    )
    assert f"{stall_min} minutes" in flat, (
        f"{_rel(path)} does not state the {stall_min}-minute stall threshold, "
        f"so the agent cannot tell what its silence will be read as."
    )
    for status in (fs.STATUS_STALLED, fs.STATUS_NO_PROGRESS, fs.STATUS_DONE):
        assert f"`{status}`" in text, (
            f"{_rel(path)} does not name the `{status}` status its ledger "
            f"produces. The consequence is what makes the protocol stick."
        )
    assert ">>" in text, (
        f"{_rel(path)} does not name an append (`>>`) as the write mechanism. "
        f"research-auditor.md is granted Read/Grep/Glob/Bash and no Write tool, "
        f"so a shell append is the only mechanism all four streams share -- and "
        f"a rewrite would truncate the history the lead reads."
    )


def test_start_md_f2_roster_names_the_stream_progress_ledgers() -> None:
    """D-053 / GI-001: the roster half of the durable placement.

    GI-001's shape is "the four agent files PLUS start.md's F2 roster". The
    roster is where the lead learns it has nothing to paste -- without that,
    the reactive `no_ledger` channel reads like the intended mechanism rather
    than the stopgap it is.
    """
    text = _read(START_MD)
    flat = _flat(START_MD)

    for wire_id in sorted(fs.INSPECT_STREAM_AGENT_IDS):
        assert f"{wire_id}.jsonl" in text, (
            f"start.md's F2 roster does not name `{wire_id}.jsonl`. The lead "
            f"has no way to know which ledger belongs to which stream, and the "
            f"wire-id-not-filename rule is invisible."
        )
    assert f"foundry-archive/{{run}}/{fs.PROGRESS_DIR_NAME}/" in text, (
        "start.md's F2 roster does not name the progress directory the streams "
        "write to."
    )
    assert "`## Progress ledger` section of each of the four agent files" in flat, (
        "start.md's F2 roster does not say the instruction lives in the agent "
        "files. That sentence is what distinguishes a baked-in protocol "
        "(GI-001, AC-003) from a per-run injection the lead has to perform."
    )
    assert "no per-run configuration and nothing for you to paste" in flat, (
        "start.md's F2 roster does not state that the progress protocol needs "
        "no per-run configuration, so a lead may re-introduce the injection "
        "step the durable placement exists to retire."
    )


# ---------------------------------------------------------------------------
# GRIND cycle 6 -- prose that named a server surface the server does not have
#
# Seven defects, one class: shipped protocol prose describing a tool, a
# parameter or a rule that the shipped Python does not implement that way.
# D-061 (casting_commit never named, so the evidence gate never engaged),
# D-064/D-065 (the no_ledger row describes one of two producers and is false
# for the other), D-067 (the `done` row states a precedence that is now
# conditional), D-068 (target_kind never named, so the comment-prose refusal
# is unreachable), D-070 (teammate.md blesses a statement Foundry-Fix
# hard-refuses), D-072 (two skills call a tool that does not exist), D-073
# (coverage-diff.md claims a validator check that was never written).
#
# Every pin below is DERIVED from the shipped code -- the tool registry, a
# schema's properties, a producer's real record shape, a gate driven over the
# prose's own example. D-067 is why: a hand-typed literal pin defended a stale
# sentence for four cycles and pushed the fixer back toward the wrong wording.
# A pin that cannot notice the behaviour moving is not a pin.
# ---------------------------------------------------------------------------


def _tool_schema(name: str) -> dict:
    """Return one registered tool's inputSchema, via the real ``list_tools``.

    Mirrors ``test_fix_gate.py``'s idiom. Going through ``list_tools()``
    rather than reading the module source is what makes these assertions
    track the schema a client actually receives.
    """
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    match = next((t for t in tools if t.name == name), None)
    assert match is not None, (
        f"{name} is not a registered MCP tool. Registered: "
        f"{sorted(t.name for t in tools)}."
    )
    return match.inputSchema


def _registered_tool_names() -> set[str]:
    from foundry_mcp import server as foundry_server

    return {t.name for t in asyncio.run(foundry_server.list_tools())}


# ---------------------------------------------------------------------------
# D-061 -- the acceptance step that never named casting_commit
# ---------------------------------------------------------------------------

# The numbered acceptance list plus the paragraph under it. Bounded so a
# `casting_commit` mention anywhere ELSE in a 650-line file cannot satisfy the
# assertion -- the defect was precisely that the parameter was documented in a
# nested schema description no lead ever reads while the step they DO follow
# omitted it.
_ACCEPTANCE_HEADING = "**Acceptance check per casting:**"
_ACCEPTANCE_END = "### F2: INSPECT"


def _acceptance_block() -> str:
    text = _read(START_MD)
    start = text.find(_ACCEPTANCE_HEADING)
    assert start != -1, (
        f"start.md has no {_ACCEPTANCE_HEADING!r} section. That block is the "
        f"literal sequence the lead follows to accept a casting; without it "
        f"there is nothing for this module to pin."
    )
    end = text.find(_ACCEPTANCE_END, start)
    assert end != -1, "start.md's acceptance block is not followed by the F2 section."
    return " ".join(text[start:end].split())


def test_start_md_acceptance_step_names_every_accept_casting_parameter() -> None:
    """D-061: the gate's reachable half had no carrier in the protocol.

    ``Foundry-Accept-Casting`` has always accepted ``casting_commit`` and gates
    the ENTIRE evidence block on ``casting_commit is not None``. The string
    appeared in zero markdown files anywhere under plugins/foundry, and
    start.md's acceptance step -- the literal call the lead copies -- listed
    four arguments. So EVID-01 and EVID-02 never ran on a real acceptance, and
    the run that shipped FR-017 accepted six castings with `evidence_provenance`
    absent from all six, which start.md's own rule calls the structural signal
    that the gate did not run.

    Derived from the schema's property names rather than a hand-typed
    ``"casting_commit"``: a parameter added to the tool and left out of the
    protocol step is the same defect one rename later, and this is what makes
    that fail here instead of shipping.
    """
    block = _acceptance_block()
    documented = sorted(_tool_schema("Foundry-Accept-Casting")["properties"])

    # Asserted against the CALL's own argument list, not the surrounding
    # paragraph. Mutation-checked: a version of this test that searched the
    # whole block stayed green when `casting_commit` was deleted from the call
    # and left in the prose around it -- which is the defect almost exactly,
    # since start.md's call line is the thing a lead copies and the schema
    # description a lead never reads already documented the parameter.
    call = re.search(r"`Foundry-Accept-Casting\(([^`]*)\)`", block)
    assert call is not None, (
        "start.md's acceptance block no longer contains a literal "
        "`Foundry-Accept-Casting(...)` call. The step the lead copies IS the "
        "call; prose describing it is not a substitute."
    )
    args = call.group(1)
    missing = [p for p in documented if p not in args]
    assert not missing, (
        f"start.md's `Foundry-Accept-Casting(...)` call does not pass "
        f"{missing}, which the tool accepts. A parameter absent from the call "
        f"is a parameter no lead passes -- and an optional one that gates a "
        f"whole verification phase is then dead in every real run while the "
        f"gate still returns ok:true. Add it to the call, and say in the block "
        f"where its value comes from."
    )


def test_start_md_says_what_supplying_casting_commit_engages() -> None:
    """D-061: naming the argument is not enough -- it must say what it buys.

    ``casting_commit`` is optional in the schema purely as a
    backwards-compatibility shim. Nothing in the tool's own description
    mentions evidence re-execution, so a lead who sees an optional parameter
    with no stated consequence omits it, and the silent-skip path is the one
    every run took.
    """
    block = _acceptance_block()
    for token in ("EVID-01", "EVID-02", "evidence_provenance"):
        assert token in block, (
            f"start.md's acceptance step never mentions {token!r}. Supplying "
            f"casting_commit is what engages server-side evidence "
            f"re-execution and per-requirement binding; omitting it skips "
            f"both SILENTLY and still returns ok:true. A lead who is not told "
            f"that reads the parameter as optional in the ordinary sense."
        )


# ---------------------------------------------------------------------------
# D-064 / D-065 -- one status, two producers, two record shapes
# ---------------------------------------------------------------------------

_LIVENESS_HEADING = "## TEAMMATE LIVENESS"
_LIVENESS_END = "## CONTEXT MANAGEMENT"


def _liveness_section() -> str:
    text = _read(START_MD)
    start = text.find(_LIVENESS_HEADING)
    assert start != -1, f"start.md has no {_LIVENESS_HEADING!r} section."
    end = text.find(_LIVENESS_END, start)
    assert end != -1, "start.md's liveness section is not followed by CONTEXT MANAGEMENT."
    return " ".join(text[start:end].split())


def _liveness_row(status: str) -> str:
    """Return the one liveness-table row whose first cell is ``status``.

    D-079: asserting a row's rule against the whole SECTION is what made the
    previous `done` pin defeatable. PROVE deleted the row's exception clause
    outright and added one unrelated sentence containing the word "unless"
    elsewhere in the section, and the pin stayed green. A qualifier only
    qualifies the claim it sits next to, so it has to be asserted in the cell
    that makes the claim -- never in the section that contains it.
    """
    text = _read(START_MD)
    start = text.find(_LIVENESS_HEADING)
    assert start != -1, f"start.md has no {_LIVENESS_HEADING!r} section."
    end = text.find(_LIVENESS_END, start)
    assert end != -1, "start.md's liveness section is not followed by CONTEXT MANAGEMENT."

    prefix = f"| `{status}` |"
    rows = [
        line for line in text[start:end].splitlines() if line.startswith(prefix)
    ]
    assert len(rows) == 1, (
        f"start.md's liveness table has {len(rows)} rows beginning {prefix!r}, "
        f"expected exactly 1. Every status in the vocabulary gets one row, and "
        f"a lead reading two rows for one status cannot tell which rule applies."
    )
    return rows[0]


def _statuses_named_in(cell: str) -> set[str]:
    """Return the liveness statuses written as code spans inside ``cell``.

    Same filter as ``_statuses_named_after``: a code span that is not a member
    of the real vocabulary is not a status, and a MISSPELLED one is dropped
    rather than accepted, so a typo turns a set comparison red.
    """
    return {
        token
        for token in _CODE_SPAN_RE.findall(cell)
        if token in fs.PROGRESS_STATUSES
    }


def _both_no_ledger_shapes(tmp_path: Path) -> tuple[dict, dict]:
    """Build one real record from each ``no_ledger`` producer.

    Not a fixture and not hand-written dicts: the whole point is that these
    come out of the shipped functions, so a producer that starts or stops
    carrying a field moves the assertion with it.
    """
    now = datetime.now(timezone.utc)
    fdir = tmp_path / "foundry-archive" / "d65-shapes"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "state.json").write_text(
        json.dumps(
            {
                "phase": fs.INSPECT_PHASE,
                "phase_times": {
                    fs.INSPECT_PHASE: {
                        "started_at": (now - timedelta(hours=3)).isoformat()
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    run_rel = f"foundry-archive/{fdir.name}"
    streams = fs._missing_stream_records(
        fdir, now, fs.STALL_THRESHOLD_SECONDS, run_rel
    )
    teammates = fs._missing_teammate_records(
        {
            fs._agent_id_for_casting("1"): {
                "moment": now - timedelta(hours=1),
                "record": {"phase": "grind"},
            }
        },
        now,
        run_rel,
    )
    assert streams and teammates, (
        "one of the two no_ledger producers returned nothing, so the shape "
        "comparison below would be vacuous."
    )
    return streams[0], teammates[0]


def test_start_md_no_ledger_row_describes_both_producers(tmp_path: Path) -> None:
    """D-064 / D-065: the row made a universal claim false for most rows.

    9c1e69d gave ``foundry_liveness`` a SECOND ``no_ledger`` producer --
    ``_missing_teammate_records``, for F1/F3 dispatched teammates -- beside the
    existing F2 stream producer. start.md's row was not updated and still said
    "The run is in F2 and expects this stream agent to be writing ... Carries
    that stream's `progress_protocol` block on the record." Driven on the live
    thunder-viper run at F3, all six no_ledger rows were teammate rows and
    `progress_protocol` was absent from 6 of 6: a lead following the row was
    told to append a block that existed on no row it was holding.

    The asymmetry is deliberate on the code's side --
    ``_missing_teammate_records``' own docstring says so -- so the pin is the
    SYMMETRIC DIFFERENCE of the two real record shapes. Every field that
    distinguishes the producers must be named in the prose. A producer that
    grows or drops a distinguishing field fails here rather than quietly
    making the row wrong again.
    """
    stream_row, teammate_row = _both_no_ledger_shapes(tmp_path)
    assert stream_row["status"] == teammate_row["status"] == fs.STATUS_NO_LEDGER

    section = _liveness_section()

    distinguishing = sorted(set(stream_row) ^ set(teammate_row))
    assert distinguishing, (
        "the two no_ledger producers now emit identical field sets, so the "
        "two-shape prose may be obsolete -- re-read start.md before deleting "
        "anything, but this assertion has stopped meaning what it says."
    )
    missing = [field for field in distinguishing if field not in section]
    assert not missing, (
        f"start.md's liveness section never names {missing}. Those fields are "
        f"exactly what tells the two `{fs.STATUS_NO_LEDGER}` producers apart "
        f"({sorted(set(stream_row) - set(teammate_row))} on a stream row, "
        f"{sorted(set(teammate_row) - set(stream_row))} on a teammate row). A "
        f"lead who cannot tell which shape they are holding follows the wrong "
        f"remedy -- which is D-065 exactly."
    )


def test_start_md_no_ledger_row_names_both_producing_phases() -> None:
    """D-064: the row named F2 only, and the teammate case is F1/F3.

    Derived from ``INSPECT_PHASE`` and ``TEAMMATE_DISPATCH_PHASES`` so a
    routing change cannot leave the prose behind.
    """
    section = _liveness_section()
    for phase in (fs.INSPECT_PHASE, *sorted(fs.TEAMMATE_DISPATCH_PHASES)):
        assert phase in section, (
            f"start.md's liveness section never mentions {phase}, which is one "
            f"of the phases that produces a `{fs.STATUS_NO_LEDGER}` row "
            f"(streams in {fs.INSPECT_PHASE}, teammates in "
            f"{sorted(fs.TEAMMATE_DISPATCH_PHASES)}). Naming only one of them "
            f"is what made the row false for the other."
        )
    assert "instructions" in section, (
        "start.md's liveness section never points at the response's "
        "`instructions` field. That field is where foundry_liveness puts the "
        "kind-keyed remedy -- the teammate row carries no progress_protocol "
        "block, so `instructions` is the only place its remedy exists."
    )


# ---------------------------------------------------------------------------
# D-067 -- the `done` precedence, and its supersede exception
# ---------------------------------------------------------------------------


def _activate(project_root: Path, run_name: str) -> Path:
    fdir = project_root / "foundry-archive" / run_name
    fdir.mkdir(parents=True, exist_ok=True)
    foundry_state.set_active_run(run_name)
    return fdir


def _write_terminal_ledger(fdir: Path, agent: str, age_seconds: float) -> None:
    moment = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    pdir = fdir / fs.PROGRESS_DIR_NAME
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / f"{agent}.jsonl").write_text(
        json.dumps(
            {
                "timestamp": moment.isoformat(),
                "phase": "grind",
                "step": "fix applied",
                fs.TERMINAL_FIELD: True,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_dispatch(fdir: Path, casting_id: int, age_seconds: float) -> None:
    moment = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    with (fdir / "spawns.log").open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp": moment.isoformat(),
                    "casting_id": casting_id,
                    "phase": "grind",
                    "prompt_path": f"castings/casting-{casting_id}-prompt.md",
                }
            )
            + "\n"
        )


def _liveness_row_for(project_root: Path, fdir: Path, agent: str) -> dict:
    (fdir / "state.json").write_text(json.dumps({"phase": "F3"}), encoding="utf-8")
    result = fs.foundry_liveness(project_root=str(project_root))
    by_agent = {r["agent"]: r for r in result["agents"]}
    assert agent in by_agent, f"{agent} absent from {sorted(by_agent)}"
    return by_agent[agent]


def _terminal_ledger_cases(tmp_path: Path) -> dict[str, dict]:
    """Drive every case a ledger ending in a terminal line can produce.

    The four cases are the cross product of the ``superseded`` branch's two
    real conditions (foundry_spawn.py#_agent_liveness_record):

        superseded = dispatch is not None and dispatch["moment"] > last["moment"]

    where ``dispatch`` is ``overdue.get(...)`` and ``overdue`` is filtered to
    dispatches at least ``threshold`` old (foundry_spawn.py#foundry_liveness).
    So a dispatch overrules a terminal line only when it is BOTH newer than
    that line AND already past the threshold -- and the case that separates
    the two conditions is "newer but young", which still reports `done`.

    Everything the pin below asserts about start.md's prose is computed from
    these four real records. Nothing is typed twice.
    """
    agent = fs._agent_id_for_casting("1")
    ledger_age = 5 * fs.STALL_THRESHOLD_SECONDS
    cases = {
        # No dispatch at all: the plain precedence, D-052's original ruling.
        "no dispatch": None,
        # Dispatched, but BEFORE the terminal line -- the agent finished the
        # work it was last asked for, so terminal still wins.
        "dispatch older than the terminal line": 9 * fs.STALL_THRESHOLD_SECONDS,
        # Dispatched AFTER the terminal line but younger than the threshold.
        # This is the case D-080 filed: the prose promised an overrule here
        # and the code does not perform one.
        "newer dispatch inside the threshold": fs.STALL_THRESHOLD_SECONDS * 0.01,
        # Dispatched after the terminal line AND overdue -- both conditions.
        "newer dispatch past the threshold": 3 * fs.STALL_THRESHOLD_SECONDS,
    }

    rows: dict[str, dict] = {}
    for index, (label, dispatch_age) in enumerate(cases.items()):
        root = tmp_path / f"case{index}"
        fdir = _activate(root, f"d80-case{index}")
        try:
            _write_terminal_ledger(fdir, agent, age_seconds=ledger_age)
            if dispatch_age is not None:
                _write_dispatch(fdir, 1, age_seconds=dispatch_age)
            rows[label] = _liveness_row_for(root, fdir, agent)
        finally:
            foundry_state.clear_active_run()
    return rows


def test_start_md_done_row_states_the_supersede_exception(tmp_path: Path) -> None:
    """D-067: the precedence became conditional and the row did not.

    9c1e69d added ``superseded = dispatch is not None and dispatch["moment"] >
    last["moment"]`` and gated the terminal branch on ``not superseded``. The
    row kept the unqualified sentence "it outranks every age check", and the
    pin that defended it was the bare literal -- so the prose and the pin were
    wrong together and a fixer correcting either one broke the other.

    D-079 then found the replacement pin defeatable in the same shape it was
    written to close. Its prose half was two literals -- ``"outranks every age
    check" in section`` and ``"unless" in section.lower()`` -- and the second
    was a bare token searched against the WHOLE liveness section. PROVE deleted
    the exception clause from the `done` row outright, restoring D-067's stale
    absolute, appended one unrelated sentence containing the word "unless"
    elsewhere in the section, and the test passed green.

    So both weaknesses are fixed here. Every assertion is SCOPED to the `done`
    row's own cell -- a qualifier only qualifies the claim beside it -- and
    every required token is COMPUTED from the four real records in
    ``_terminal_ledger_cases``, never typed:

      * the field set the prose must name is the symmetric difference between
        the "newer but young" row and the "newer and overdue" row, which is
        exactly what the age condition adds;
      * the statuses the row may name are exactly the statuses the four drives
        actually produced.

    A code change on either side moves the requirement with it: drop the
    overdue filter and the young case stops reporting `done`, collapsing the
    difference and firing the guard below; make `no_progress` reachable and the
    status-set comparison names the prose that must learn about it.

    One literal survives on purpose. "outranks every age check" is the sentence
    D-052 bought, and no derivation can check an English claim of precedence --
    the drives prove the claim is TRUE, and the literal proves it is still
    STATED. Scoped to the row, it is not the defect D-079 filed.
    """
    rows = _terminal_ledger_cases(tmp_path)
    row = _liveness_row(fs.STATUS_DONE)

    # --- what the code actually does, established before reading any prose ---
    observed = {label: record["status"] for label, record in rows.items()}
    overruled = "newer dispatch past the threshold"
    young = "newer dispatch inside the threshold"

    assert observed[overruled] != fs.STATUS_DONE, (
        f"a terminal line followed by a newer, overdue dispatch reported "
        f"`{observed[overruled]}` -- expected anything but `{fs.STATUS_DONE}`. "
        f"If the supersede branch was deliberately removed, start.md's `done` "
        f"row must lose its exception clause in the same change; a false "
        f"qualification is as bad as the false absolute D-067 filed."
    )
    for label in ("no dispatch", "dispatch older than the terminal line", young):
        assert observed[label] == fs.STATUS_DONE, (
            f"the case '{label}' reported `{observed[label]}`, not "
            f"`{fs.STATUS_DONE}`. All three are cases where the terminal line "
            f"still wins, and start.md's row promises they do: the precedence "
            f"itself (D-052), a dispatch that preceded the line, and a "
            f"re-dispatch younger than the threshold (D-080). A code change "
            f"that overrules any of them must rewrite the row in the same pass."
        )

    # --- the prose must state the precedence, and state it conditionally ---
    assert "outranks every age check" in row, (
        "start.md's `done` row no longer states the precedence at all. With no "
        "precedence a lead reads a 3600s-old finished agent as a stalled one, "
        "which is D-052. The fix for D-067 was to QUALIFY this sentence, not "
        "to delete it."
    )

    discriminators = sorted(set(rows[overruled]) ^ set(rows[young]))
    assert discriminators, (
        f"the overruled row and the young-dispatch row now carry identical "
        f"field sets, so nothing on a row tells the lead which of the two "
        f"supersede conditions it is looking at. Either the threshold filter "
        f"on `overdue` was removed -- in which case start.md's `done` row must "
        f"lose its age qualifier in the same change -- or the dispatch "
        f"annotation was, in which case the row must stop pointing at fields "
        f"it no longer has. Re-read the row before touching this assertion."
    )
    unnamed = [field for field in discriminators if f"`{field}`" not in row]
    assert not unnamed, (
        f"start.md's `done` row never names {unnamed}. Those fields are exactly "
        f"what the SECOND supersede condition adds to a row (present on "
        f"{sorted(set(rows[overruled]) - set(rows[young]))}, absent on "
        f"{sorted(set(rows[young]) - set(rows[overruled]))}), and a row that "
        f"states only the first condition promises an overrule the tool does "
        f"not perform -- D-080 exactly. An agent re-dispatched inside the "
        f"threshold still reports `{fs.STATUS_DONE}`."
    )

    # --- the remedy may name only the statuses the drives actually reached ---
    named = _statuses_named_in(row)
    reachable = set(observed.values())
    assert named == reachable, (
        f"start.md's `done` row names {sorted(named)}; a ledger ending in a "
        f"terminal line can only ever report {sorted(reachable)}. Naming a "
        f"status it cannot reach sends the lead hunting for a row that will "
        f"never appear -- the row claimed `{fs.STATUS_NO_PROGRESS}` for six "
        f"cycles, and an overruled agent's last line always predates a "
        f"dispatch that is already past the threshold, so the "
        f"`{fs.STATUS_STALLED}` branch is reached first every time."
    )


# ---------------------------------------------------------------------------
# D-068 -- target_kind, the refusal's only carrier
# ---------------------------------------------------------------------------

# The filing surfaces the four stream agents call. Both accept `target_kind`;
# the refusal in `foundry_add_defect` fires ONLY on a declared
# `target_kind == "comment"`, so prose that never names the field leaves the
# whole comment-prose channel unreachable.
_FILING_TOOLS = ("Foundry-Defect", "Foundry-Sync")
_TARGET_KIND = "target_kind"


def test_target_kind_is_a_real_parameter_on_both_filing_surfaces() -> None:
    """Floor check: the pins below are vacuous if the field was renamed.

    ``Foundry-Defect`` carries it as a top-level property; ``Foundry-Sync``
    carries it per-item inside the defects array. Both are read so a rename on
    either surface fails here, naming the prose that must follow it.
    """
    defect_props = _tool_schema("Foundry-Defect")["properties"]
    assert _TARGET_KIND in defect_props, (
        f"Foundry-Defect no longer advertises {_TARGET_KIND!r}. If the field "
        f"was renamed, rename it in the four stream agent files and start.md's "
        f"F2 roster in the same change -- the prose is the field's only carrier."
    )
    # The array property is located by SHAPE, not by name: it is spelled
    # `findings` on the wire while the agent prose and the ledger call the same
    # records defects, and hardcoding either spelling makes this floor check
    # fail for a reason that has nothing to do with target_kind.
    sync_props = _tool_schema("Foundry-Sync")["properties"]
    arrays = [
        name
        for name, prop in sync_props.items()
        if prop.get("type") == "array" and isinstance(prop.get("items"), dict)
    ]
    assert len(arrays) == 1, (
        f"Foundry-Sync now has {arrays} array properties; this check assumed "
        f"exactly one (the per-finding records). Point it at the right one."
    )
    sync_item_props = sync_props[arrays[0]]["items"]["properties"]
    assert _TARGET_KIND in sync_item_props, (
        f"Foundry-Sync's per-finding items no longer advertise "
        f"{_TARGET_KIND!r}, so a stream syncing a batch has no way to declare "
        f"a comment subject and the refusal cannot engage on that path."
    )


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_names_the_target_kind_parameter(path: Path) -> None:
    """D-068: four files promised a refusal none of them could reach.

    All four already carry "``Foundry-Defect`` and ``Foundry-Sync`` refuse it
    as a defect server-side". That refusal fires only when the CALLER declares
    ``target_kind == "comment"`` -- a deliberate fail-safe, since refusing a
    defect is itself a demotion and an undeclared subject must never be
    demoted. ``schemas/vocab.py`` states the resulting obligation outright:
    "Callers MUST populate this -- its absence is a caller bug, not a licence
    to demote." Nothing carried that MUST. The parameter appeared in ZERO
    markdown files under plugins/foundry, so across six cycles and 67 filed
    defects not one record carried it and observations.json was never created.

    This is the same shape as D-020 and D-061: a working mechanism with no
    documented caller. The prose is the carrier, so the prose is what is pinned.
    """
    text = _read(path)
    assert _TARGET_KIND in text, (
        f"{_rel(path)} never names the `{_TARGET_KIND}` parameter, yet it "
        f"promises that Foundry-Defect and Foundry-Sync refuse comment prose "
        f"server-side. That refusal reads this field and nothing else, so as "
        f"written the promise is false in practice: every finding this stream "
        f"files lands in defects.json regardless of the split above it."
    )
    flat = _flat(path)
    assert '"comment"' in text or "`comment`" in text, (
        f"{_rel(path)} names `{_TARGET_KIND}` without giving the value that "
        f"engages the refusal. Only the literal 'comment' demotes; any other "
        f"value pins the finding as a defect."
    )
    for tool in _FILING_TOOLS:
        assert f"`{tool}`" in flat, (
            f"{_rel(path)}'s filing instruction does not name `{tool}`, so the "
            f"obligation to populate `{_TARGET_KIND}` is not bound to the call "
            f"that must carry it."
        )


def test_start_md_f2_roster_names_the_target_kind_parameter() -> None:
    """D-068: the roster half, mirroring GI-001's four-files-plus-roster shape."""
    text = _read(START_MD)
    assert _TARGET_KIND in text, (
        f"start.md's F2 roster never names `{_TARGET_KIND}`. The roster is "
        f"where the lead learns what the streams must send; without it the "
        f"comment-prose refusal has no carrier at the roster level either."
    )


# ---------------------------------------------------------------------------
# D-070 -- teammate.md blessed a statement the Foundry-Fix gate refuses
# ---------------------------------------------------------------------------

# teammate.md's worked examples, labelled so this module can drive them. The
# label is the contract: prose that drops it silently un-pins itself, which
# `test_teammate_ships_both_statement_examples` below is what catches.
_STATEMENT_EXAMPLE_RE = re.compile(r"^(REFUSED|ACCEPTED): (.+)$", re.M)


def _statement_examples() -> list[tuple[str, str]]:
    return _STATEMENT_EXAMPLE_RE.findall(_read(TEAMMATE))


def test_teammate_ships_both_statement_examples() -> None:
    """Floor check: the drive below asserts nothing if the examples vanished."""
    labels = {label for label, _ in _statement_examples()}
    assert labels == {"REFUSED", "ACCEPTED"}, (
        f"teammate.md's adjacent-path section carries examples labelled "
        f"{sorted(labels)}; both REFUSED and ACCEPTED are required. They are "
        f"the only worked statements a GRIND teammate has, and the test below "
        f"is what proves they still behave as labelled."
    )


def test_teammate_statement_examples_survive_the_shipped_gate() -> None:
    """D-070: shipped prose and the shipped gate contradicted each other.

    teammate.md told the teammate: "'Nothing else calls it' is a legitimate
    statement when the grep supports it. Say that, and say which grep showed
    it." ``_STATEMENT_NON_ANSWERS``' fourth pattern is
    ``re.compile(r"\\bnothing\\s+else\\b", re.I)`` -- UNANCHORED, so appending
    the grep the prose asked for does not help. Driven at the MCP boundary,
    both the bare sentence and the sentence-plus-grep were REFUSED, and the
    teammate was left with no instruction for what to write instead. That
    fires in GRIND, the phase where every defect must close, on the one
    legitimate case where a symbol has a single caller.

    Existence-only pins covered this block and neither noticed, because they
    assert the section is present and never that its example survives the
    gate. This drives teammate.md's OWN sentences through the real
    ``_statement_problem``, so the file cannot bless a refused form or warn
    against an accepted one.
    """
    for label, statement in _statement_examples():
        problem = orch._statement_problem(statement, "", "")
        if label == "REFUSED":
            assert problem is not None, (
                f"teammate.md labels this statement REFUSED, but the shipped "
                f"gate ACCEPTS it: {statement!r}. Either the gate loosened or "
                f"the example drifted -- a teammate warned off a working form "
                f"writes something worse instead."
            )
        else:
            assert problem is None, (
                f"teammate.md offers this statement as ACCEPTED and the "
                f"shipped gate refuses it: {statement!r} -- {problem}. A "
                f"teammate copying the file's own example cannot close its "
                f"defect, which is D-070 exactly."
            )


def test_teammate_tells_a_single_caller_fixer_what_to_declare() -> None:
    """D-070: the refusal needed a remedy, not just a prohibition.

    "Who else calls this" is one of three axes the same section demands. When
    callers are genuinely exhausted the other two are not, and that is the
    instruction the file was missing -- without it a teammate blocked by the
    gate has nowhere to go.
    """
    flat = _flat(TEAMMATE)
    assert "Nothing else calls it\" is a legitimate statement" not in flat, (
        "teammate.md still blesses \"Nothing else calls it\" as a legitimate "
        "adjacent-path statement. Foundry-Fix hard-refuses it, with or without "
        "the grep the sentence promises will rescue it."
    )
    assert "transitions" in flat and "concurrently" in flat, (
        "teammate.md no longer points a single-caller fixer at the other two "
        "axes (transitions, concurrent work). Prohibiting the denial without "
        "naming the alternative leaves the teammate stuck in the phase where "
        "every defect must close."
    )


# ---------------------------------------------------------------------------
# D-072 -- prose naming a tool that does not exist
# ---------------------------------------------------------------------------

# Every markdown surface that instructs an agent or the lead to CALL a tool.
# Same corpus shape as the `source:` sweep above, plus references/.
_TOOL_CALLING_PROSE = (
    sorted(AGENTS.glob("*.md"))
    + sorted(COMMANDS.glob("*.md"))
    + sorted(REFERENCES.glob("*.md"))
    + sorted(FOUNDRY_ROOT.glob("skills/*/SKILL.md"))
)

# A `Foundry-`/`Forge-` tool token as shipped prose writes one. Trailing
# hyphens are stripped so `Foundry-Stream-based` does not read as a tool name.
_TOOL_TOKEN_RE = re.compile(r"\b(?:Foundry|Forge)-[A-Z][A-Za-z]*(?:-[A-Z][A-Za-z]*)*")


def test_every_tool_named_in_shipped_prose_is_registered() -> None:
    """D-072: two skills instructed a call to a tool that does not exist.

    ``skills/trace/SKILL.md`` and ``skills/sight/SKILL.md`` both told their
    stream to "mark the stream complete via `Foundry-Mark-Stream-Complete`".
    No such tool is registered -- the real one is ``Foundry-Stream`` -- so
    neither stream could ever record a completion, and the per-cycle coverage
    roll-up those records feed stayed empty for both. Their absence reads as
    no coverage rather than as a broken call, which is why it survived.

    This is the same class as D-061 and D-068 and the sharpest instance of it:
    there the parameter was omitted, here the NAME is wrong. The sweep is
    derived from the live registry over every markdown surface that instructs
    a call, so it would have caught this the day it was written and covers the
    whole class going forward.
    """
    registered = _registered_tool_names()
    unknown: dict[str, list[str]] = {}
    for path in _TOOL_CALLING_PROSE:
        bad = sorted(
            {
                token
                for token in _TOOL_TOKEN_RE.findall(_read(path))
                if token not in registered
            }
        )
        if bad:
            unknown[_rel(path)] = bad
    assert not unknown, (
        f"shipped prose names tool(s) the MCP server does not register: "
        f"{unknown}. Registered tools are "
        f"{sorted(n for n in registered if n.startswith(('Foundry-', 'Forge-')))}. "
        f"An unregistered name is not a soft failure -- the call never "
        f"reaches a handler, so the instructed step silently does nothing."
    )


def test_the_tool_name_sweep_reads_real_prose() -> None:
    """Floor check: a regex that matches nothing forbids nothing."""
    found = {
        token
        for path in _TOOL_CALLING_PROSE
        for token in _TOOL_TOKEN_RE.findall(_read(path))
    }
    for expected in ("Foundry-Defect", "Foundry-Stream", "Foundry-Accept-Casting"):
        assert expected in found, (
            f"the tool-name sweep did not match {expected!r} anywhere in "
            f"{len(_TOOL_CALLING_PROSE)} prose files. The regex no longer "
            f"matches the shape shipped prose writes tool names in, so the "
            f"guard has gone vacuous."
        )


@pytest.mark.parametrize(
    "path",
    (FOUNDRY_ROOT / "skills" / "trace" / "SKILL.md",
     FOUNDRY_ROOT / "skills" / "sight" / "SKILL.md"),
    ids=lambda p: p.parent.name,
)
def test_stream_completion_instructions_name_every_required_argument(path: Path) -> None:
    """D-072's second half: the right tool called with an invalid argument set.

    Correcting only the NAME still yields a call jsonschema rejects at the MCP
    boundary. ``Foundry-Stream`` requires ``stream``, ``cycle`` and
    ``items_checked``; the trace instruction passed no ``stream`` and no
    ``cycle``, the sight instruction no ``cycle``. Both halves had to land
    together, and the required list is read from the schema so a new required
    field fails here instead of shipping an uncallable instruction.
    """
    required = sorted(_tool_schema("Foundry-Stream")["required"])
    flat = _flat(path)
    assert "`Foundry-Stream`" in flat, (
        f"{_rel(path)} no longer names `Foundry-Stream` as the tool that marks "
        f"its stream complete."
    )

    # Scoped to the call CLAUSE -- the "via `Foundry-Stream` with X, Y, Z"
    # sentence -- and matched as an ARGUMENT rather than as a word. Two
    # mutation results shaped this. A version searching the whole file for
    # `cycle` stayed green after the argument was deleted, because
    # skills/sight/SKILL.md says "cycle" nine times in unrelated prose
    # ("console-logs-cycle-{N}", "the next GRIND cycle"). A version reading a
    # 400-char window then stayed green when `stream` was deleted from the
    # trace call, because the NEXT sentence ("`stream`, `cycle` and
    # `items_checked` are all REQUIRED") re-mentions it -- prose ABOUT the
    # parameters standing in for the parameters, which is D-061's shape
    # exactly. Cutting at the sentence end is what keeps the assertion on the
    # arguments the instruction actually tells the agent to pass.
    windows = []
    for m in re.finditer(r"`Foundry-Stream`", flat):
        tail = flat[m.end() : m.end() + 400]
        stop = tail.find(". ")
        windows.append(tail if stop == -1 else tail[:stop])
    assert windows, f"{_rel(path)} names no `Foundry-Stream` call to scope to."

    def _documents(window: str, field: str) -> bool:
        return any(
            span == field or span.startswith((f"{field}=", f"{field}:"))
            for span in re.findall(r"`([^`]+)`", window)
        )

    best_missing = min(
        ([f for f in required if not _documents(w, f)] for w in windows),
        key=len,
    )
    assert not best_missing, (
        f"{_rel(path)}'s `Foundry-Stream` instruction does not pass "
        f"{best_missing}, which the tool REQUIRES ({required}). A call "
        f"omitting a required field is rejected by jsonschema at the MCP "
        f"boundary, so the stream records no coverage for the cycle -- and its "
        f"absence from the roll-up reads as no coverage rather than as a "
        f"broken call, which is why D-072 survived so long."
    )


# ---------------------------------------------------------------------------
# D-073 -- a cite-policy rollout that asserted a validator check nobody wrote
# ---------------------------------------------------------------------------


def _drive_dimension8(
    tmp_path: Path,
    run_name: str,
    coverage_list: list[str],
    source_inventory: list[str] | None = None,
) -> list[dict]:
    """Run the real migration validator and return Dimension 8's issues.

    Builds a genuine MIGRATION manifest and calls ``foundry_validate_castings``
    -- no stubbing of the check under test, because the claim in the prose is
    about what the shipped validator does.
    """
    fdir = tmp_path / foundry_state.ARCHIVE_DIR / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    manifest: dict = {
        "spec_type": "MIGRATION",
        "castings": [
            {
                "id": 1,
                "title": "Coverage entry shape",
                "spec_text": "",
                "observable_truths": ["a", "b", "c"],
                "key_files": ["src/one.go"],
                "must_haves": {
                    "truths": ["ports the thing"],
                    "artifacts": [{"path": "src/one.go"}],
                    "key_links": [],
                    "coverage_list": coverage_list,
                },
            }
        ],
    }
    if source_inventory is not None:
        manifest["source_inventory"] = source_inventory
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (fdir / "spec.md").write_text("", encoding="utf-8")

    foundry_state.set_active_run(run_name)
    try:
        result = foundry_validate_castings(str(tmp_path))
    finally:
        foundry_state.clear_active_run()
    return result["dimensions"]["migration_coverage"]["issues"]


def test_a_respelled_coverage_entry_is_caught_only_under_an_inventory(
    tmp_path: Path,
) -> None:
    """D-081: "caught nowhere in the pipeline" is false in one branch.

    D-073's fix replaced one false claim with a narrower one that is still
    false when a manifest declares a ``source_inventory``. Dimension 8 then
    cross-checks the coverage entries against that inventory
    (foundry_validate.py#foundry_validate_castings), so a re-spelled entry
    orphans its inventory counterpart and the counterpart is flagged. The
    claim held only for manifests with no inventory, and the prose stated it
    unconditionally.

    Both branches are driven here because the qualifier is the whole point:
    without the no-inventory half, a later fixer reading only the flagged case
    would "correct" the prose back into claiming a guarantee that most
    manifests do not have. The token the prose must use is taken from the
    issue the validator really emitted, not typed.
    """
    inventory_entry = "src/one.go:TestAlpha"
    respelled = "src/one.go:TestAlpah"

    without = _drive_dimension8(tmp_path, "d81-no-inventory", [respelled])
    assert not without, (
        f"a re-spelled coverage entry was flagged with no `source_inventory` "
        f"declared ({without}). coverage-diff.md says a manifest without an "
        f"inventory gets no spelling check at all -- if one was added, that "
        f"sentence must be rewritten in the same change."
    )

    with_inventory = _drive_dimension8(
        tmp_path, "d81-inventory", [respelled], source_inventory=[inventory_entry]
    )
    flagged = [issue for issue in with_inventory if issue.get("entry") == inventory_entry]
    assert flagged, (
        f"a re-spelled coverage entry left `{inventory_entry}` unclaimed and "
        f"the declared `source_inventory` did not flag it ({with_inventory}). "
        f"That branch is the ONLY thing that catches a re-spelling, so if it "
        f"was removed coverage-diff.md may go back to saying a re-spelled "
        f"entry is caught nowhere -- update the prose rather than leaving it "
        f"describing a check that no longer runs."
    )

    flat = _flat(COVERAGE_DIFF)
    for token in ("source_inventory", flagged[0]["issue"]):
        assert token in flat, (
            f"coverage-diff.md never names `{token}`. The re-spelling claim is "
            f"conditional on it: with an inventory declared the validator "
            f"emits `{flagged[0]['issue']}` for the orphaned counterpart, and "
            f"an agent told the shape is checked nowhere will not go looking "
            f"for the one issue that would have caught its typo."
        )
    assert "caught nowhere in the pipeline. A **re-spelled**" in flat, (
        "coverage-diff.md states the 'caught nowhere' claim without splitting "
        "the colonless case from the re-spelled one. Colonless is caught "
        "nowhere unconditionally; re-spelled is caught under a declared "
        "`source_inventory`. One unqualified sentence covering both is D-081."
    )


def test_the_coverage_entry_shape_is_unenforced(tmp_path: Path) -> None:
    """D-073: coverage-diff.md claimed enforcement that does not exist.

    Commit b33d1c4, this effort's own cite-policy rollout, wrote into
    coverage-diff.md that the ``source_file:symbol`` shape is one "which the
    manifest validator requires verbatim". Dimension 8 iterates the
    coverage_list and its only per-entry check is ``isinstance(entry, str)``;
    the shape appears in an issue DETAIL string, never in a condition. A bare
    ``"foo"`` is a perfectly valid entry as far as the validator is concerned.

    This matters because FR-004 makes the cite policy turn on which forms are
    MECHANICALLY guaranteed -- an agent told a shape is validator-enforced
    trusts an unchecked field, and there is no resolution guard here of the
    kind AC-006 provides for `#Symbol` cites. So the fix was to soften the
    sentence, and this is what keeps the softened version honest: if the shape
    check is ever added, this test goes red and coverage-diff.md may claim
    enforcement again.
    """
    run_name = "d73-coverage-shape"
    fdir = tmp_path / foundry_state.ARCHIVE_DIR / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(
            {
                "spec_type": "MIGRATION",
                "castings": [
                    {
                        "id": 1,
                        "title": "Colonless coverage entry",
                        "spec_text": "",
                        "observable_truths": ["a", "b", "c"],
                        "key_files": ["src/one.go"],
                        "must_haves": {
                            "truths": ["ports the thing"],
                            "artifacts": [{"path": "src/one.go"}],
                            "key_links": [],
                            # No colon, no path, no symbol -- and accepted.
                            "coverage_list": ["foo"],
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (fdir / "spec.md").write_text("", encoding="utf-8")

    foundry_state.set_active_run(run_name)
    try:
        result = foundry_validate_castings(str(tmp_path))
    finally:
        foundry_state.clear_active_run()

    issues = result["dimensions"]["migration_coverage"]["issues"]
    shape_issues = [i for i in issues if i.get("issue") == "invalid_coverage_entry"]
    assert not shape_issues, (
        f"Dimension 8 now rejects the colonless coverage_list entry 'foo' "
        f"({shape_issues}). If a shape check was deliberately added, "
        f"coverage-diff.md's softened sentence may assert enforcement again -- "
        f"update the prose in the same change rather than leaving the file "
        f"understating a guarantee it now has."
    )


def test_coverage_diff_does_not_claim_validator_enforcement() -> None:
    """D-073: the colon argument stands; the appeal to enforcement does not.

    The sentence's own reasoning leaned on the false half -- the colon form is
    safe from line drift BECAUSE the validator pins it verbatim. The colon
    argument is sound on its own merits, so the fix keeps it and drops the
    claim, and says plainly that nothing checks the shape.
    """
    flat = _flat(COVERAGE_DIFF)
    assert "the manifest validator requires verbatim" not in flat, (
        "coverage-diff.md still claims the manifest validator requires the "
        "`source_file:symbol` shape verbatim. Dimension 8 checks only that "
        "each entry is a string (see "
        "test_the_coverage_entry_shape_is_unenforced), so the claim is false "
        "and an agent that trusts it trusts an unchecked field."
    )
    assert "only that each `coverage_list` entry is a string" in flat, (
        "coverage-diff.md does not say what the validator ACTUALLY checks. "
        "Dropping the false claim without stating the real guarantee leaves "
        "the next reader to re-derive it, and the last one who tried wrote "
        "the claim this test exists to prevent."
    )
