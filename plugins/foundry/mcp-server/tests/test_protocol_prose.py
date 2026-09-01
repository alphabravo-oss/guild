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

from pathlib import Path

import pytest

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
SYMBOL_AUTHORITATIVE_SITES = (ASSAYER, TRACER, RESEARCH_AUDITOR, TEAMMATE)


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
