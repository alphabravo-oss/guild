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

import json
import re
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab

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
    # The two-axis answer is the whole point: a bare heartbeat cannot
    # distinguish these, and A-025 names the distinction explicitly.
    for status in ("`progressing`", "`no_progress`", "`stalled`", "`unknown`"):
        assert status in text, (
            f"start.md's liveness guidance does not document the {status} "
            f"status. The closed vocabulary is the lead's decision surface."
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
    """D-040 boundary: `source_file:symbol` is a validated input contract.

    ``foundry_validate`` requires every ``coverage_list`` entry to be shaped
    ``path/to/file.go:TestSymbolName``. That colon separates a path from a
    SYMBOL, never from a line, so the placement rule has no quarrel with it --
    and "convert every colon" would have broken a server-enforced contract.
    """
    text = _read(COVERAGE_DIFF)
    assert "`source_file:symbol`" in text, (
        "coverage-diff.md no longer documents the `source_file:symbol` shape "
        "that foundry_validate enforces on every coverage_list entry."
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
