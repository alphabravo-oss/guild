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

That is a ruling about the SPLIT, and GI-001's tier rule is the deliberate
exception to it rather than a drift from it: FR-030 requires the replacement
wording to be pinned, and patterns/PATTERNS.md rules a rule shared across the
stream agents word-identical in all of them. There is one sentence to pin for
that rule, not one voice per file to respect, and
``test_stream_agents_share_one_tier_rule_verbatim`` is what holds the
word-identity the clause pins beside it then read. The roster it sweeps is
``DEFECT_FILING_AGENTS``, DERIVED rather than typed -- see the D-017 comment
above it for why a hand-maintained list cannot fail on the file it forgot. Both dispositions live in
this module on purpose; the section comment above that test says which ruling
gets which treatment and why.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.tools import foundry as foundry_doors
from foundry_mcp.tools import evidence as evidence_doors
from foundry_mcp.tools import foundry_handoff
from foundry_mcp.tools import foundry_spawn as fs
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools import rosters
from foundry_mcp.tools.foundry_validate import foundry_validate_castings

# fallout GI-010 — the monolith this module used to import is DELETED, and
# the three private symbols pinned below are reached at the module-contract
# homes casting 2's symbol map records: `_statement_problem` with the
# fix-gate block, `_recorded_prove_roster` with the streams block,
# `_maybe_skip_trace` with the width blocks. The call sites stay QUALIFIED
# by module rather than importing the three names bare, because a bare
# `_statement_problem(...)` no longer says which of the thirteen
# orchestration modules owns it, and this module reaches private symbols
# across module boundaries on purpose. Re-binding one short alias to one of
# the three, or shipping a re-export shim under the monolith's old name,
# would have kept this import green and is exactly the facade GI-010
# refuses.
from foundry_mcp.tools.orchestration import directives, fix_gate, streams, width

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

# The command files that CREATE or RELOAD a run, derived rather than typed out.
#
# D-086: the guard-install pins were written against start.md alone, so when
# GI-002 said "every target repo a run touches", only one of the two doors into
# the loop actually installed the guard -- /foundry:resume walked straight into
# CAST/GRIND with whatever hook the repo happened to have, which for the
# pre-4.9.0 archives STEP 3 exists to serve is none at all. A hardcoded
# ``(START_MD, RESUME_MD)`` tuple would fix today's two files and leave the next
# entrypoint free to repeat the same omission, so the roster is DERIVED.
#
# ``Foundry-Init`` is the membership test because it is the tool that creates or
# reloads run state: a command that calls it is, by definition, a command after
# which teammates commit into the target repo, which is exactly the population
# GI-002 names. Commands that only describe the loop (help.md mentions
# ``Foundry-Next``) or that never enter it (setup/status/stop/update) do not
# call it and are correctly excluded. ``test_run_entry_roster_is_not_vacuous``
# below is the floor check -- a derived roster that silently empties out would
# make every pin that sweeps it vacuously green.
RUN_ENTRY_COMMANDS = tuple(
    sorted(
        (p for p in COMMANDS.glob("*.md") if "Foundry-Init" in p.read_text(encoding="utf-8")),
        key=lambda p: p.name,
    )
)

# The four F2 INSPECT streams that carry the observation/defect split in their
# own prose. The split is a property of all four together: a stream still filing
# comment prose as a defect re-opens the loop this effort exists to close.
#
# This is NOT the roster of everything that files into the defect ledger --
# `agents/coverage-diff.md` does too, and start.md's F2 roster states that it is
# bound by the split without restating it. The tier and class pins therefore
# sweep the DERIVED `DEFECT_FILING_AGENTS` further down, not this tuple (D-017).
# Keep the two apart: widening this one would demand a split paragraph in a file
# the command prose says does not carry one.
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


def test_run_entry_roster_is_not_vacuous() -> None:
    """Floor check for the derived RUN_ENTRY_COMMANDS roster.

    Every guard pin below sweeps a roster computed by globbing ``commands/``
    and grepping for ``Foundry-Init``. If that derivation ever returns an empty
    or truncated set -- the directory moves, the tool is renamed, the glob stops
    matching -- the parametrised tests below do not fail, they simply stop
    running, and GI-002 goes unpinned while the suite stays green. This test is
    what makes that failure loud.

    The two named members are asserted by name because they are the two doors
    into the foundry loop that exist today and both are known to need the
    guard; extra members are welcome (a future entrypoint joining the roster is
    the point of deriving it) and are deliberately not forbidden here.
    """
    assert RUN_ENTRY_COMMANDS, (
        "RUN_ENTRY_COMMANDS derived an EMPTY roster from "
        f"{_rel(COMMANDS)}/*.md. Every guard pin that sweeps it is now "
        "vacuously green. Either the commands directory moved, or Foundry-Init "
        "was renamed -- fix the derivation, do not delete the pins."
    )
    for expected in (START_MD, RESUME_MD):
        assert expected in RUN_ENTRY_COMMANDS, (
            f"{_rel(expected)} is no longer in the derived run-entry roster, so "
            f"nothing below checks that it installs the commit guard. It calls "
            f"Foundry-Init (or it did) and is therefore a command after which "
            f"teammates commit into the target repo -- exactly the population "
            f"GI-002 covers."
        )


@pytest.mark.parametrize("path", RUN_ENTRY_COMMANDS, ids=lambda p: p.name)
def test_run_entry_command_allow_lists_the_guard_installer(path: Path) -> None:
    """GI-002 / D-086: an install step the allow-list refuses is not a step.

    resume.md shipped 4.9.0 with neither the entry nor the step; the entry is
    pinned separately from the invocation because the two fail independently --
    a command can name the installer in its prose and still be unable to run
    it, which reads as installed and is not.
    """
    text = _read(path)
    assert "Bash(${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-guard.sh:*)" in text, (
        f"{_rel(path)} does not allow-list install-commit-guard.sh. The lead "
        f"cannot invoke the installer from this command even when the prose "
        f"tells it to, so the target repo silently stays unguarded."
    )


@pytest.mark.parametrize("path", RUN_ENTRY_COMMANDS, ids=lambda p: p.name)
def test_run_entry_command_installs_the_commit_guard(path: Path) -> None:
    """GI-002: EVERY target repo a run touches gets the shipped guard.

    Swept over both doors into the loop, not just start.md: D-086 was exactly
    this pin holding on one file while /foundry:resume walked into CAST/GRIND
    with no guard at all.
    """
    text = _read(path)
    assert '"${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-guard.sh"' in text, (
        f"{_rel(path)} no longer invokes the commit-guard installer (GI-002). "
        f"Without the install step the guard ships but is never installed, so "
        f"target repos keep whatever hook they had."
    )
    assert "${CLAUDE_PLUGIN_ROOT}/hooks/pre-commit-guard.sh" in text, (
        f"{_rel(path)} no longer names the guard asset the installer places."
    )
    assert "`git diff --cached`" in text, (
        f"{_rel(path)} no longer states that the guard judges the index only. A "
        f"working-tree guard (`git diff HEAD`) fires on a peer's unstaged WIP, "
        f"which is the GI-002 violation."
    )


@pytest.mark.parametrize("path", RUN_ENTRY_COMMANDS, ids=lambda p: p.name)
def test_run_entry_command_references_the_installer_through_the_plugin_root(
    path: Path,
) -> None:
    """Convention: a shipped asset is referenced through ${CLAUDE_PLUGIN_ROOT},
    never an absolute path -- the plugin cache is version-namespaced, so an
    absolute path pins one installed version and breaks on upgrade."""
    text = _read(path)
    for line in text.splitlines():
        if "install-commit-guard.sh" in line or "pre-commit-guard.sh" in line:
            assert "${CLAUDE_PLUGIN_ROOT}" in line, (
                f"{_rel(path)}: guard asset referenced without "
                f"${{CLAUDE_PLUGIN_ROOT}}: {line!r}"
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
    # D-017's absence half. The file's own no-severity bullet used to close on
    # "the `orphans` array is a separate channel from `defects`, not a weaker
    # tier of it" -- correct prose in the release that wrote it, and a
    # collision in the release that gave `tier` a closed vocabulary and made it
    # required on every filing. One word carrying the abolished meaning in the
    # same file that now mandates the new one teaches the reader the wrong one,
    # because the graded reading is the one the English invites.
    for abolished in ("weaker tier", "lesser tier", "tier of it", "severity tier of"):
        assert abolished not in text, (
            f"coverage-diff.md uses {abolished!r} -- `tier` in the ABOLISHED "
            f"graded sense, in the file that now requires `tier` as the "
            f"evidence axis (GI-001 / FR-004). Say CHANNEL when you mean "
            f"channel; the word `tier` has exactly one meaning left."
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
#:
#: WHY `tier` IS ADMITTED (GI-001 / AC-009)
#: ----------------------------------------
#: `tier` was added by widening this set on purpose, through exactly the ritual
#: the failure message below describes: the vocabulary landed in
#: `schemas/vocab.py` as `DEFECT_TIERS` first, and this set was widened after.
#: It is admitted because it grades EVIDENCE, not effort. `LIVE` records that
#: the filing stream drove the door and observed the wrong result; `LATENT`
#: records that it derived the finding and found no reachable instance, and
#: said what it drove in `reproduction_attempted`. Neither value decides how
#: much a fix is worth, neither licenses deferring one, and both are defects
#: that get fixed -- the tier decides only which GATE a still-open instance
#: blocks. The failure it answers is the one thunder-viper measured: a run
#: cannot tell a defect somebody watched fail from one derived off a scan, so
#: every finding costs a full GRIND cycle to disprove and the cycle count never
#: converges. The alternative -- leaving the axis out of the schemas and
#: carrying it in prose -- was rejected because a normative ```json block
#: outranks the prose beside it, which is D-041's whole finding.
#: `test_skill_schema_tier_enum_equals_the_vocab_tiers` below is what keeps the
#: admission honest: `tier` is allowed to be an enum, but only the enum
#: `vocab.DEFECT_TIERS` declares.
_ALLOWED_ENUM_KEYS = frozenset({"classification", "type", "verdict", "tier"})

#: What a skill's `tier` enum must equal. Derived from the module in the same
#: discipline as `_EXPECTED_TYPE_ENUM` above: re-typing `{"LIVE", "LATENT"}`
#: here would be a second copy of a closed vocabulary, free to drift from the
#: one the filing doors actually validate against.
_EXPECTED_TIER_ENUM = frozenset(vocab.DEFECT_TIERS)

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
        f"Every defect gets fixed, so a WORK-EFFORT axis has nothing left to "
        f"decide -- and one reintroduced under a new key ('priority', "
        f"'impact', 'weight') is the same abolished axis wearing a different "
        f"name. `tier` is not that axis and is not an example of it: it is the "
        f"EVIDENCE axis GI-001 added, admitted here on purpose and held to "
        f"vocab.DEFECT_TIERS by "
        f"test_skill_schema_tier_enum_equals_the_vocab_tiers. If a genuinely "
        f"new closed vocabulary is needed, add it to schemas/vocab.py first "
        f"and widen _ALLOWED_ENUM_KEYS deliberately, the way `tier` was."
    )


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_schema_tier_enum_equals_the_vocab_tiers(path: Path) -> None:
    """GI-001 / AC-009: `tier` is admitted as an enum, but only the right enum.

    Widening ``_ALLOWED_ENUM_KEYS`` for `tier` buys the key a licence to carry
    a closed enum and nothing more. Without this, a skill could advertise
    ``["LIVE", "LATENT", "COSMETIC"]`` and pass every guard in this module --
    a third member is exactly how the abolished axis comes back, one value at
    a time, under a name that has already been blessed. Compared as a SET
    against the module, in the discipline of ``_EXPECTED_TYPE_ENUM``: a
    substring check cannot tell a missing member from a present one and cannot
    see an extra one at all.
    """
    schema = _findings_schema(path)
    actual = dict(_iter_enums(schema)).get("tier")
    assert actual is not None, (
        f"{_rel(path)}'s findings schema has no `tier` enum. FR-004 makes the "
        f"filing stream responsible for the evidence axis; a block with no "
        f"slot for it tells the stream the field is optional, and the filing "
        f"doors then refuse every finding it emits."
    )
    assert actual == _EXPECTED_TIER_ENUM, {
        "file": _rel(path),
        "advertised_but_not_a_tier": sorted(actual - _EXPECTED_TIER_ENUM),
        "tier_the_skill_never_learned": sorted(_EXPECTED_TIER_ENUM - actual),
        "why": (
            "The skill's tier enum must equal vocab.DEFECT_TIERS exactly. A "
            "value the skill advertises but vocab rejects is dropped at the "
            "MCP boundary before it reaches the ledger; a vocab member the "
            "skill omits is an evidence grade the stream has no legal way to "
            "file. A THIRD member is the work-effort axis returning under a "
            "key that has already been blessed."
        ),
    }


#: The clauses the rewritten no-severity paragraph must carry in BOTH schema-
#: bearing skills. One assertion per claim: a single pin on the whole paragraph
#: would fail on a reflow that changed no words, and would say nothing about
#: WHICH claim went missing.
_SKILL_TIER_CLAUSES = (
    (
        "The work-effort grade is banned by name",
        "the paragraph no longer bans the abolished axis BY NAME. FR-030 makes "
        "the replacement wording the implementer's choice ONLY on condition "
        "that the work-effort grade stays banned by name -- a paragraph that "
        "introduces `tier` without naming what stays forbidden reads as the "
        "grade coming back under a new label.",
    ),
    (
        "no `minor`, no `major`, no `critical`, no `severity`, no `priority`, no `impact`",
        "the paragraph no longer enumerates the banned spellings. Naming one "
        "of them bans one of them; the enumeration is what makes the ban a "
        "ban rather than a grep for the word severity.",
    ),
    (
        "grade a finding by whether you actually drove it or only derived it from a "
        "scan, and never by how much work it would take to fix",
        "the paragraph no longer states BOTH halves of the distinction in one "
        "sentence. Split across two sentences a reader takes the first and "
        "leaves the second, which is how the abolished axis returns beside the "
        "new one instead of in place of it.",
    ),
    (
        "`LIVE` means you drove the door and observed the wrong result",
        "the paragraph no longer says what LIVE means. A closed vocabulary "
        "whose members are unexplained is a vocabulary streams guess at.",
    ),
    (
        "`LATENT` means you derived the finding and found no reachable instance",
        "the paragraph no longer says what LATENT means.",
    ),
    (
        "MUST carry a `reproduction_attempted` statement naming what you drove and "
        "what it found",
        "the paragraph no longer requires the reproduction_attempted statement "
        "on a LATENT finding (FR-004 / CT-001). The server refuses that filing, "
        "so a skill that does not say so sends its stream into a refusal it "
        "cannot read its way out of.",
    ),
    (
        "a security-property claim can NEVER be `LATENT`",
        "the paragraph no longer rules out a LATENT security-property claim "
        "(CT-003). That is a denylist entry, not a judgement call: the filing "
        "is refused and fires a tripwire.",
    ),
    (
        "`SECURITY_PROPERTY_CLAIM`",
        "the paragraph no longer names the denylist class the refusal reports, "
        "so a stream that hits it cannot tell which rule it broke.",
    ),
    (
        "`#DEFECT_TIERS`",
        "the paragraph no longer cites schemas/vocab.py as the source of truth "
        "for the tier vocabulary, so the enum in the block beside it becomes a "
        "second copy free to drift.",
    ),
    (
        "`tier` buys the stream no discretion over anything else",
        "the paragraph lost its no-exceptions clause. Every rule in this "
        "register closes on one; without it `tier` reads as a licence.",
    ),
)


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
@pytest.mark.parametrize("clause,why", _SKILL_TIER_CLAUSES, ids=lambda v: v[:40])
def test_skill_no_severity_paragraph_states_the_evidence_axis(
    path: Path, clause: str, why: str
) -> None:
    """GI-001 / AC-009 / FR-030: the rewritten paragraph, clause by clause."""
    assert clause in _flat(path), f"{_rel(path)}: {why}"


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_key_constraints_carry_the_evidence_axis(path: Path) -> None:
    """FR-004: the constraint list is where a stream looks for its obligations.

    The paragraph above lives beside the JSON block, at the bottom of a
    350-line file. The constraints list is the summary a stream re-reads while
    working, and a rule absent from it is a rule that applies only to readers
    who got that far.
    """
    flat = _flat(path)
    assert "- **Grade the evidence, never the effort**" in flat, (
        f"{_rel(path)}'s constraints list no longer carries the evidence-axis "
        f"rule. FR-004 makes the filing stream responsible for `tier`; a "
        f"constraint list that omits it leaves that obligation stated once, in "
        f"a paragraph about a JSON block."
    )
    assert "every finding carries `tier`" in flat, (
        f"{_rel(path)}'s constraint does not make `tier` required on every "
        f"finding. An optional evidence axis is an axis streams omit, and the "
        f"filing doors then refuse the finding."
    )


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_schemas_require_the_new_axes(path: Path) -> None:
    """D-041: an optional classification is a classification streams omit.

    D-003 added `class` to the same list. FR-007 requires a non-empty class on
    every filing and states that "agent prose and report formats require it";
    AC-010 makes one classless finding refuse a whole Foundry-Sync batch. Both
    blocks described the axis as "Optional root-cause" and left it out of this
    list, so a stream emitting exactly the shape its own skill documents was
    refused at the filing door one surface later.
    """
    text = _read(path)
    assert '"required": ["id", "classification", "type", "class", "tier", "file", "symbol", "description"]' in text, (
        f"{_rel(path)}'s required list does not demand classification, type, "
        f"class, tier and symbol. `severity` was REQUIRED before this fix -- "
        f"replacing a required field with optional ones weakens the contract "
        f"instead of correcting it. If `class` is what went missing: the "
        f"filing door refuses a classless finding (FR-007/AC-010), so a block "
        f"that leaves it optional documents a shape the door rejects. If "
        f"`tier` is: D-063 drove that gap end to end -- a finding carrying "
        f"exactly the old list validated clean through Validate-Report and was "
        f"then refused by Foundry-Sync ('findings[0].tier: Invalid tier: "
        f"None'), which refuses the WHOLE batch. This block is the derivation "
        f"source for schemas/findings.py's own required list, so the two move "
        f"together or the validator goes laxer than the door it feeds."
    )
    assert "Optional root-cause" not in text, (
        f"{_rel(path)}'s `class` description calls the axis optional again. "
        f"The door refuses a filing without it, and schemas/findings.py "
        f"mirrors this block's required list by its own stated derivation "
        f"rule -- so the adjective and the list have to move together."
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
    """D-041: 'not on the critical path' was severity under another name.

    D-008 added the second half. GI-001 requires the rewritten no-severity
    prose to be RE-PINNED, and the FAIL bullet was rewritten to close the new
    exemption `tier` makes available -- a stream reading "LATENT" as "found by
    reasoning, so not really broken" reinvents the off-the-critical-path
    exemption on the axis that replaced severity. The rewrite shipped
    unpinned, which is how the first exemption survived long enough to need
    removing.
    """
    text = _flat(PROVE_SKILL)
    assert "there is no off-the-critical-path exemption" in text, (
        "prove/SKILL.md's verdict rules downgraded a non-VERIFIED item to WARN "
        "when it sat off the critical path. assayer.md's 'EVERY non-VERIFIED "
        "verdict is a defect, no exceptions' admits no such exemption, and an "
        "importance test by any other name is the axis D-041 removes."
    )
    assert (
        "and no `LATENT` exemption either, because `tier` records the evidence "
        "behind a defect and never whether it is worth fixing" in text
    ), (
        "prove/SKILL.md's FAIL rule no longer closes the LATENT exemption. "
        "FAIL keys on `classification`, not on `tier`: both tiers are defects "
        "and both get fixed. A verdict rule silent about the evidence axis "
        "invites a stream to WARN on the findings it only derived, which is "
        "the off-the-critical-path exemption again on a newer axis."
    )


def test_trace_skill_states_plumber_findings_are_defects_not_a_tier() -> None:
    """D-041: the prose twin of the schema's severity enum.

    D-009 added the evidence-axis half. This paragraph is one of the surfaces
    GI-001 names for the no-severity rewrite, and the rewritten sentence
    shipped with no exact-substring pin at all -- so the words that distinguish
    the CHANNEL claim from the EVIDENCE claim could be dropped in a later edit
    without a single test noticing.
    """
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
    assert "This is a channel statement, not a severity one" in text, (
        "trace/SKILL.md no longer says which AXIS the PL-N rule is about. "
        "Without it the paragraph reads as a claim that broken workflows "
        "matter more than other defects -- the severity tier restated in "
        "prose, which is what D-041 removed from this file."
    )
    assert (
        "The evidence axis is separate again — a PL-N flow you actually drove "
        "and watched fail is `LIVE`, one you derived from the wiring with no "
        "reachable path is `LATENT` and carries a `reproduction_attempted` "
        "statement — and neither tier is a grade on how much the fix is worth."
        in text
    ), (
        "trace/SKILL.md's PL-N paragraph no longer distinguishes the evidence "
        "axis from the channel. GI-001 requires the no-severity prose to state "
        "the LIVE/LATENT distinction wherever it is rewritten, and a PL-N "
        "flow is exactly where a stream is tempted to file what it read off "
        "the wiring as though it had driven it."
    )


#: Phrasings that USE the work-effort axis rather than banning it. The four
#: stream agents and the two schema-bearing skills all carry a rule spelling
#: the axis out to forbid it -- "no `minor`, no `major`, no `critical`, no
#: `severity`, no `priority`, no `impact`" -- so a bare search for the word
#: matches the prohibition itself and can never be an assertion. These are the
#: comparative shapes the word only takes when a document is RANKING two
#: defects against each other, which is the move the rule forbids.
_APPROVING_SEVERITY_USES = (
    "same severity as",
    "high severity",
    "higher severity",
    "low severity",
    "lower severity",
    "more severe",
    "less severe",
)


@pytest.mark.parametrize(
    "path",
    STREAM_AGENTS + SCHEMA_BEARING_SKILLS,
    ids=lambda p: p.parent.name if p.name == "SKILL.md" else p.stem,
)
def test_no_stream_document_uses_the_severity_axis_approvingly(path: Path) -> None:
    """D-006 / GI-001 / FR-030: banning the word and then using it is worse
    than never banning it.

    agents/tracer.md shipped "MISPLACED is a defect, same severity as MISSING
    or UNWIRED" one screen above its own rule banning the grade BY NAME. The
    four stream agents are pinned to carry that rule word-identically, and
    this sentence sat outside the shared span, so every exact-substring pin
    on the rewrite passed while the file contradicted it.

    A reader resolves a document that contradicts itself in its own favour --
    the same reasoning `test_trace_skill_states_plumber_findings_are_defects_
    not_a_tier` records for the "high severity" claim -- so the guard is over
    the whole file, not over the rewritten span.
    """
    text = _flat(path)
    found = [phrase for phrase in _APPROVING_SEVERITY_USES if phrase in text]
    assert not found, (
        f"{_rel(path)} ranks defects against each other using {found}. "
        f"GI-001 requires this file's no-severity prose to state the "
        f"LIVE/LATENT distinction and FR-030 requires the work-effort grade "
        f"to stay banned BY NAME. Say the two defects are equal without "
        f"reaching for the abolished axis to say it -- tracer.md's Level 4 "
        f"rule is the worked example: 'exactly as much as MISSING or "
        f"UNWIRED -- every defect gets fixed, and no grade ranks one of them "
        f"under another.'"
    )


def test_tracer_places_misplaced_beside_the_other_defects_without_grading() -> None:
    """D-006's replacement, pinned so the point survives the rewrite.

    Deleting the offending clause is not enough: the sentence existed to say
    that a placement defect is not a lesser one, and a rewrite that drops the
    claim along with the word loses what Level 4 was asserting.
    """
    text = _flat(TRACER)
    assert (
        "**MISPLACED is a defect,** exactly as much as MISSING or UNWIRED — "
        "every defect gets fixed, and no grade ranks one of them under another."
        in text
    ), (
        "tracer.md's Level 4 no longer states that a placement defect ranks "
        "with the others. The clause it replaced said so using the abolished "
        "axis; saying nothing at all invites a reader to treat "
        "ARCHITECTURAL_PLACEMENT as the tidy-up class."
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
        problem = fix_gate._statement_problem(statement, "", "")
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


# ---------------------------------------------------------------------------
# GRIND cycle 8 -- D-084: the rule that decides WHICH channel a finding lands in
# ---------------------------------------------------------------------------
#
# D-068 got `target_kind` into the five carriers, so a stream could finally
# reach the comment-prose refusal. D-084 is the half that decides whether the
# refusal ACCEPTS: `vocab.is_spec_required_behaviour_claim` returns True on a
# non-empty `spec_ref` as its FIRST statement, with no inspection of what the
# finding says, and again on a requirement id anywhere in the description. A
# non-empty `spec_ref` is therefore a never-demote denylist match BY ITSELF.
#
# That behaviour is deliberate -- `Foundry-Observation`'s live tool
# description ends "Citing a requirement in spec_ref is by construction enough
# to keep a finding a defect", and test_observations.py pins it -- so it is
# not a code defect. The defect was that the PRIMARY CARRIER never said it.
# Across all five carriers the string `spec_ref` appeared ZERO times outside
# example JSON, where tracer.md and research-auditor.md showed it as a field
# to populate on every finding. A stream following its own file to the letter
# attached a `spec_ref` to a line-drift finding, was refused, fired a false
# audit tripwire, filed the finding as a defect, and never learned why -- the
# backlog US-001 exists to stop, re-entering through the report format.
#
# This is the D-078 shape (tool behaviour real, describing surface silent) at
# the carrier GI-001 names, so the prose is what is pinned -- and the pin is
# DERIVED: the field name is read from the shipped tool registry, the
# predicate is driven rather than described, and the "documented shape trips
# it" claim is read out of the agent files' own JSON examples.

_SPEC_REF = "spec_ref"

# The bolded imperative opening the new rule in all five carriers. Bullets are
# located by it so the assertions are BULLET-scoped: tracer.md and
# research-auditor.md already contained `spec_ref` in their example JSON, so a
# file-wide `in text` pin would have been vacuous for half the corpus -- which
# is precisely how the rule stayed unwritten for eight cycles.
_SPEC_REF_RULE = "An observation carries no `spec_ref` and names no requirement id."

# The exact finding D-084 drove over the real MCP boundary, varying ONLY
# spec_ref. Held as one constant so the three-way below differs in nothing
# else.
_DRIFT_FINDING = {
    "description": (
        "The comment above _mark_phase cites foundry.py:230 but that writer "
        "now sits at line 244."
    ),
    "target_kind": "comment",
}


def _rules_bullet(path: Path, imperative: str) -> str:
    """The one Rules bullet opening with `imperative`, flattened and bounded.

    Bounded at the NEXT bullet so a token living in a neighbouring rule -- or
    in an example JSON block elsewhere in the file -- cannot satisfy a pin
    about this one. Flattened first, because where a markdown reflow put the
    line break is not a property this module has any business pinning.
    """
    flat = _flat(path)
    marker = f"- **{imperative}**"
    start = flat.find(marker)
    if start == -1:
        return ""
    tail = flat[start + len(marker) :]
    stop = tail.find("- **")
    return marker + (tail if stop == -1 else tail[:stop])


def _example_records(path: Path) -> list[dict]:
    """Every JSON object nested anywhere in a file's normative examples.

    The fences are anchored (``^```json`` ... ``^``` ``) because an unanchored
    non-greedy match stops at the first ``` INSIDE a block and hands back
    unparseable text.
    """
    found: list[dict] = []
    for block in re.findall(r"^```json\n(.*?)^```$", _read(path), re.S | re.M):
        stack: list[object] = [json.loads(block)]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                found.append(node)
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    return found


def test_spec_ref_is_a_real_parameter_on_every_filing_surface() -> None:
    """Floor check: every pin below is vacuous if the field was renamed.

    Mirrors the ``target_kind`` floor check above, and for the same reason --
    the prose is the field's only carrier, so a rename on the wire has to fail
    HERE, naming the five files that must follow it.
    """
    for tool in ("Foundry-Defect", "Foundry-Observation"):
        assert _SPEC_REF in _tool_schema(tool)["properties"], (
            f"{tool} no longer advertises {_SPEC_REF!r}. If the field was "
            f"renamed, rename it in the four stream agent files and start.md's "
            f"F2 roster in the same change."
        )
    # Located by SHAPE, exactly as the target_kind floor check locates it: the
    # array is spelled `findings` on the wire while the prose and the ledger
    # both call the records defects.
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
    assert _SPEC_REF in sync_props[arrays[0]]["items"]["properties"], (
        f"Foundry-Sync's per-finding items no longer advertise {_SPEC_REF!r}, "
        f"so a stream syncing a batch has no way to cite a requirement and the "
        f"rule the five carriers state is about a field that no longer exists."
    )


def test_a_bare_spec_ref_is_by_itself_a_never_demote_match() -> None:
    """D-084's drive, over the real dispatchers rather than a description of them.

    One unchanged description, varying only ``spec_ref``. Without it the
    finding is a textbook observation; with a requirement cited it is refused
    and the tripwire fires. Nothing about the finding's CONTENT changed
    between the two, which is the whole point -- and the fact the carriers now
    have to state.
    """
    clean = dict(_DRIFT_FINDING)
    assert vocab.never_demote_class(clean) is None, (
        f"a comment-prose finding with no {_SPEC_REF} is no longer demotable. "
        f"The five carriers now instruct streams to file exactly this shape as "
        f"an observation; if the denylist rejects it, the instruction is wrong."
    )
    assert vocab.observation_class(clean) == vocab.LINE_DRIFT_CITE, (
        f"the canonical line-drift finding no longer classifies as "
        f"{vocab.LINE_DRIFT_CITE}; this drive no longer exercises the split."
    )

    for ref in ("FR-004", "US-002", "research/k8s.md#testing"):
        cited = dict(_DRIFT_FINDING, spec_ref=ref)
        assert (
            vocab.never_demote_class(cited) == vocab.SPEC_REQUIRED_BEHAVIOUR_CLAIM
        ), (
            f"{_SPEC_REF}={ref!r} no longer makes this finding undemotable. If "
            f"the predicate was deliberately narrowed so a bare {_SPEC_REF} is "
            f"no longer a claim by itself, the five carriers say it still is -- "
            f"soften them in the same change, and re-check "
            f"test_observations.py, which pins the boundary behaviour."
        )


def test_a_requirement_id_in_the_description_is_undemotable_too() -> None:
    """The rule's second half: the denylist also reads the DESCRIPTION.

    Stating only the ``spec_ref`` half would leave a stream free to write the
    requirement into its prose instead and hit the same refusal, so both
    halves are stated in all five carriers and both are driven here.
    """
    cited = dict(
        _DRIFT_FINDING,
        description=_DRIFT_FINDING["description"] + " (see FR-004)",
    )
    assert _SPEC_REF not in cited, "this case must isolate the description branch"
    assert (
        vocab.never_demote_class(cited) == vocab.SPEC_REQUIRED_BEHAVIOUR_CLAIM
    ), (
        "a requirement id inside the description is no longer a never-demote "
        "match. All five carriers instruct streams to keep requirement ids out "
        "of an observation's description on the strength of this branch."
    )


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_states_the_spec_ref_rule(path: Path) -> None:
    """D-084: the denylist named a category no stream could evaluate.

    All four already say the denylist covers "a spec-required-behaviour
    claim". None said that a bare ``spec_ref`` IS one -- so a stream had no
    way to know the phrase reaches any finding that cites a requirement, and
    its own report shape attaches one by default.
    """
    bullet = _rules_bullet(path, _SPEC_REF_RULE)
    assert bullet, (
        f"{_rel(path)} has no `{_SPEC_REF_RULE}` rule in its Rules block. The "
        f"denylist rule beside it names 'a spec-required-behaviour claim' "
        f"without saying what makes a finding one, so a stream attaches a "
        f"{_SPEC_REF} to a line-drift finding, is refused, fires a false audit "
        f"tripwire, and files the finding as a defect it never learns about."
    )
    assert f"`{_SPEC_REF}`" in bullet, (
        f"{_rel(path)}'s observation rule does not name the `{_SPEC_REF}` "
        f"field. The field IS the rule -- naming the category without the "
        f"field is the state D-084 was filed against."
    )
    assert "requirement id" in bullet, (
        f"{_rel(path)}'s observation rule states only the {_SPEC_REF} half. "
        f"The denylist reads the description too, so a stream told only to "
        f"clear the field will write the requirement into its prose and hit "
        f"the identical refusal."
    )
    assert "by construction" in bullet, (
        f"{_rel(path)}'s observation rule does not say the match is MECHANICAL. "
        f"A stream that reads it as a judgement call will reason that its "
        f"line-drift finding is not really a behaviour claim, attach the cite, "
        f"and be refused anyway."
    )
    assert "tripwire" in bullet, (
        f"{_rel(path)}'s observation rule does not state the consequence. The "
        f"tripwire is an AUDIT signal the lead reviews, so a stream firing one "
        f"by accident costs more than the refused call."
    )
    for tool in ("`Foundry-Observation`", "`Foundry-Defect`"):
        assert tool in bullet, (
            f"{_rel(path)}'s observation rule does not name {tool}, so the rule "
            f"is not bound to a call. Both surfaces take {_SPEC_REF}; the rule "
            f"is which one it belongs on."
        )


def test_start_md_f2_roster_states_the_spec_ref_rule() -> None:
    """D-084: the roster half, mirroring GI-001's four-files-plus-roster shape."""
    text = _read(START_MD)
    flat = _flat(START_MD)
    assert _SPEC_REF in text, (
        f"start.md's F2 roster never names `{_SPEC_REF}`. The roster is where "
        f"the lead learns what the streams must send, and it is also where the "
        f"lead learns to read a SPEC_REQUIRED_BEHAVIOUR_CLAIM tripwire over a "
        f"comment-prose description as a filing bug rather than as a finding."
    )
    for phrase in ("by construction", "tripwire", "`Foundry-Observation`"):
        assert phrase in flat, (
            f"start.md's F2 roster does not state {phrase!r} in its "
            f"{_SPEC_REF} ruling, so the roster carries a weaker rule than the "
            f"four agent files it binds."
        )
    assert "no per-run configuration" in flat, (
        "start.md's F2 roster no longer states that these rules need no "
        "per-run configuration (AC-003)."
    )


def test_documented_shapes_that_carry_a_spec_ref_are_undemotable() -> None:
    """The motivating fact, read out of the agent files' OWN examples.

    This is what made D-084 a live trap rather than a documentation gap: the
    shapes the streams are told to emit populate `spec_ref`, and every such
    record is refused at `Foundry-Observation` by construction. Read from the
    files so that a stream shape gaining a `spec_ref` in future is covered the
    moment it lands, with nobody remembering to add it here.
    """
    cited = [
        (_rel(path), record)
        for path in STREAM_AGENTS
        for record in _example_records(path)
        if str(record.get(_SPEC_REF, "")).strip()
    ]
    assert cited, (
        f"no stream agent's example JSON carries a non-empty {_SPEC_REF} any "
        f"more. If the field was removed from the shapes rather than QUALIFIED, "
        f"that is the wrong fix -- {_SPEC_REF} is required on defect findings; "
        f"it is observations that must leave it empty."
    )
    for rel, record in cited:
        assert vocab.is_spec_required_behaviour_claim(record), (
            f"{rel} documents a record with {_SPEC_REF}="
            f"{record.get(_SPEC_REF)!r} that the denylist does NOT match. The "
            f"file's observation rule tells streams that citing a requirement "
            f"keeps a finding in the defect ledger by construction; if that is "
            f"no longer true for a shape the file itself ships, the rule and "
            f"the example disagree."
        )


@pytest.mark.parametrize(
    "path", (TRACER, RESEARCH_AUDITOR), ids=lambda p: p.name
)
def test_shapes_carrying_a_spec_ref_qualify_it_at_the_example(path: Path) -> None:
    """D-016's discipline applied to D-084: examples must visibly agree.

    These are the two files whose example JSON shows `spec_ref` as a field on
    a finding. Left unqualified, the shape reads as "populate this on every
    finding" -- which is exactly what a stream did before filing a comment-
    prose observation and being refused. The Rules block alone is not enough:
    the reader who copies the shape may never reach it.
    """
    flat = _flat(path)
    assert f"`{_SPEC_REF}` appears on" in flat, (
        f"{_rel(path)}'s example JSON carries `{_SPEC_REF}` without saying "
        f"which channel it belongs to. Qualify the field at the shape -- do "
        f"not delete it, it is required on defect findings."
    )
    assert "never populated on a comment-prose finding" in flat, (
        f"{_rel(path)} does not rule out `{_SPEC_REF}` on an observation at "
        f"the point the shape is shown, so the shape still reads as a field to "
        f"populate on every finding."
    )


# ---------------------------------------------------------------------------
# GI-001 / AC-009 / FR-004 / FR-007 -- the evidence tier, baked into the four
# stream agents and their report shapes
# ---------------------------------------------------------------------------
#
# thunder-viper measured the failure this answers: 162 defects over 22 GRIND
# cycles with no way to tell a defect somebody watched fail from one derived
# off a scan. Both cost a full cycle to disprove, so the cycle count never
# converged. GI-001 adds `tier` as the EVIDENCE axis -- LIVE for a finding the
# stream drove to a wrong result, LATENT for one it derived and could find no
# reachable instance of -- and abolishes nothing: the work-effort grade stays
# banned by name in the same rule, because a new axis introduced without
# naming what it is NOT is how the old one returns.
#
# WHY THESE PINS ARE WHOLE-SENTENCE, WHERE THE SPLIT'S ARE CLAUSE-LEVEL
# --------------------------------------------------------------------
# This module's docstring records that the observation/defect split is
# deliberately NOT pinned at the sentence level: each of the four files states
# it in its own voice, and pasting one paragraph into four was explicitly
# rejected for that ruling. The tier rule is the opposite case by
# construction. FR-030 requires the replacement wording to be PINNED, and
# patterns/PATTERNS.md rules that a rule shared across the four stream agents
# is word-identical in all four -- so there is one sentence to pin, not four
# voices to respect. `test_stream_agents_share_one_tier_rule_verbatim` below is
# what holds that property, and every clause pin after it is parametrised over
# STREAM_AGENTS so a partial edit that fixes three files fails.

# D-017 / D-054: the corpus of prose surfaces that must carry the tier and
# class rules is DERIVED, not typed out. GRIND-1 updated five prose files by
# hand and missed `agents/coverage-diff.md` -- a live F2 stream whose report
# shape feeds `Foundry-Sync`, which then refuses every finding in it for a
# missing `tier` and refuses the whole BATCH for one bad finding. A
# hand-maintained tuple cannot fail that way loudly: the file that was
# forgotten is exactly the file nobody adds to the list.
#
# THE DERIVATION THAT ANSWERED THAT THEN MISSED ONE ITSELF (D-054). It globbed
# `agents/*.md` and required the literal `Foundry-Sync`. Both are facts about
# where a file LIVES and which of two doors it happens to name, and neither is
# what makes a file load-bearing at the defect ledger.
# `skills/sight/SKILL.md` is a member of `vocab.DEFECT_SOURCE_IDS`, files
# through `Foundry-Defect`, and lives under `skills/` -- so it failed BOTH
# predicates and could not derive in however far its prose drifted. Driven: its
# Step 4 still instructed a call carrying no `tier`, no `class` and no
# `reproduction_attempted`, which the shipped Foundry-Defect schema refuses
# outright, and still routed findings by a work-effort grade GI-001 bans by
# name -- while every pin below stayed green over the gap. A derived roster
# that derives over the wrong corpus fails exactly as silently as a typed one.
#
# So the question the corpus asks is now the question the DOORS ask -- which
# prose surfaces INSTRUCT A FILING -- and it is asked over both directories a
# filing surface can live in. That yields two corpora, because two different
# things are true of the members:
#
#   DEFECT_FILING_SURFACES -- every surface that instructs a filing at all.
#       This is the COMPLETENESS corpus.
#       ``test_every_defect_source_id_has_a_prose_surface`` binds it to
#       ``vocab.DEFECT_SOURCE_IDS`` so a filing stream with no prose fails by
#       name, and ``test_every_filing_surface_carries_the_tier_substance``
#       holds the floor every filer owes whatever register it writes in.
#
#   DEFECT_FILING_AGENTS -- the subset whose normative example documents a
#       `defects` array, i.e. the stream-report register. patterns/PATTERNS.md
#       rules a shared rule word-identical across these, and they are what the
#       clause pins below sweep. A surface emitting a `findings` array (prove,
#       trace) or a `domains` report (temper) states the same obligations in
#       its own `## Key Constraints` voice; pasting the stream register into it
#       would be a paraphrase of a different document.
#
# `agents/teammate.md` names both tools and is correctly in the first corpus
# and not the second: it documents no `defects` array, and states the
# obligations in its own fix-and-file voice rather than the stream register.


def _documents_a_defects_array(path: Path) -> bool:
    """True when a file's normative JSON examples document a `defects` array.

    A malformed block reads as "no array" rather than raising, so one file with
    an unparseable example cannot take this module down at import time. The
    silence is safe only because the floor check below asserts the known
    members are present -- a member that vanished this way fails there, naming
    the file.
    """
    try:
        records = _example_records(path)
    except (json.JSONDecodeError, AssertionError):
        return False
    return any(isinstance(r.get("defects"), list) for r in records)


#: The two doors a prose surface can be told to file through. Naming EITHER is
#: what makes a surface load-bearing; demanding a PARTICULAR one is what let
#: sight drift for three cycles (D-054), because sight names only the first.
_FILING_TOOLS = ("Foundry-Defect", "Foundry-Sync")

SIGHT_SKILL = SKILLS / "sight" / "SKILL.md"
TEMPER_SKILL = SKILLS / "temper" / "SKILL.md"


def _surface_name(path: Path) -> str:
    """The name a surface files under: its stem, or its skill directory."""
    return path.parent.name if path.name == "SKILL.md" else path.stem


def _instructs_a_filing(path: Path) -> bool:
    """True when a surface tells its reader to call a defect-filing door."""
    text = path.read_text(encoding="utf-8")
    return any(tool in text for tool in _FILING_TOOLS)


DEFECT_FILING_SURFACES = tuple(
    sorted(
        (
            path
            for path in (*AGENTS.glob("*.md"), *SKILLS.glob("*/SKILL.md"))
            if _instructs_a_filing(path)
        ),
        key=_rel,
    )
)

DEFECT_FILING_AGENTS = tuple(
    path for path in DEFECT_FILING_SURFACES if _documents_a_defects_array(path)
)


def test_the_defect_filing_roster_is_derived() -> None:
    """Floor check: every tier and class pin below sweeps this roster."""
    missing = sorted(
        _rel(p)
        for p in (set(STREAM_AGENTS) | {COVERAGE_DIFF, SIGHT_SKILL})
        - set(DEFECT_FILING_AGENTS)
    )
    assert not missing, (
        f"{missing} no longer derive into DEFECT_FILING_AGENTS. A file drops "
        f"out by losing its filing-door mention or its example `defects` "
        f"array -- either of which is itself the D-017 defect, because the "
        f"shape a stream copies is what the door then refuses. Fix the file "
        f"rather than hard-coding the roster."
    )
    assert TEAMMATE not in DEFECT_FILING_AGENTS, (
        "agents/teammate.md derived into DEFECT_FILING_AGENTS. It states the "
        "tier and class obligations in its own fix-and-file voice, not the "
        "word-identical stream register, so sweeping it here would demand a "
        "paste that does not belong in a builder's protocol."
    )


def test_the_filing_surface_corpus_spans_both_directories() -> None:
    """D-054's floor: the corpus that missed sight was an `agents/` glob.

    The two corpora are nested -- every DEFECT_FILING_AGENTS member is a
    DEFECT_FILING_SURFACES member -- so a regression that narrowed the wider
    one back to a directory would leave every pin below green and only this
    assertion red. That is deliberate: the narrowing is invisible everywhere
    else, which is exactly how it survived three GRIND cycles the first time.
    """
    assert set(DEFECT_FILING_AGENTS) <= set(DEFECT_FILING_SURFACES), (
        "DEFECT_FILING_AGENTS is no longer a subset of DEFECT_FILING_SURFACES; "
        "the two derivations have come apart."
    )
    for expected in (TEAMMATE, PROVE_SKILL, TRACE_SKILL, TEMPER_SKILL, SIGHT_SKILL):
        assert expected in DEFECT_FILING_SURFACES, (
            f"{_rel(expected)} instructs a filing and is not in "
            f"DEFECT_FILING_SURFACES. Either the file stopped naming "
            f"{_FILING_TOOLS} -- in which case its stream now files through "
            f"nothing -- or the corpus narrowed back to one directory or one "
            f"tool name, which is D-054 exactly."
        )
    skills = {p for p in DEFECT_FILING_SURFACES if p.name == "SKILL.md"}
    agents = {p for p in DEFECT_FILING_SURFACES if p.parent == AGENTS}
    assert skills and agents, (
        f"DEFECT_FILING_SURFACES spans only one directory "
        f"({sorted(_rel(p) for p in DEFECT_FILING_SURFACES)}). A filing "
        f"surface lives under agents/ OR under skills/, and a corpus that "
        f"sees one of those is the D-054 hole re-opened."
    )


#: `vocab.DEFECT_SOURCE_IDS` members the plugin ships no prose surface for,
#: each with the reason it has none. An entry here is a CLAIM that nothing
#: reads a file to file under that id, so the test below asserts it both ways:
#: a listed id that grows a surface fails until its entry is deleted, and an
#: unlisted id with no surface fails naming the id. A bare absence -- which is
#: what sight had for three cycles -- can be neither.
_SOURCELESS_FILING_IDS = {
    "test": (
        "TEST is the project's own test command. The lead runs it and records "
        "coverage through Foundry-Stream; no agent or skill file drives it, so "
        "there is no prose surface to carry a filing rule."
    ),
    "probe": (
        "PROBE-01 is an F0.9 decompose sub-check inside commands/start.md, not "
        "a spawned stream. It files through the lead, whose own tier rules live "
        "in start.md and are pinned by tests/test_lead_prose.py."
    ),
    "test01": (
        "agents/spec-test-deriver.md writes to the `test_observations` channel "
        "and names no filing door. ASSAY is what promotes an observation into "
        "the defect ledger, and ASSAY files it with a tier like anything else."
    ),
}


def _normalise_surface_id(value: str) -> str:
    """Fold a wire id and a filename onto one spelling.

    `research_audit` is `research-auditor.md`, `flow_trace` is
    `flow-tracer.md`, `assay` is `assayer.md`. The separators and the agentive
    suffix are the only differences, so stripping non-alphanumerics and
    matching on prefix binds the two vocabularies without a hand-written map --
    which is the artefact this whole derivation exists to avoid.
    """
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _surfaces_covering(source_id: str) -> list[Path]:
    """Every filing surface whose own name claims this defect source id."""
    wanted = _normalise_surface_id(source_id)
    return [
        path
        for path in DEFECT_FILING_SURFACES
        if _normalise_surface_id(_surface_name(path)).startswith(wanted)
    ]


def test_every_defect_source_id_has_a_prose_surface() -> None:
    """D-054: a stream that files has a file telling it how, or a reason.

    `vocab.DEFECT_SOURCE_IDS` is the closed set of values a defect record's
    `source` may carry -- the server's own answer to "who filed this". Every
    one of them is something that files, so every one of them either has a
    prose surface in the corpus above (and therefore carries the substance
    floor below) or is named here with the reason it has none.

    sight was neither. It was a DEFECT_SOURCE_IDS member with a real prose
    surface that no derivation reached, and nothing anywhere said so.
    """
    assert set(_SOURCELESS_FILING_IDS) <= set(vocab.DEFECT_SOURCE_IDS), (
        f"{sorted(set(_SOURCELESS_FILING_IDS) - set(vocab.DEFECT_SOURCE_IDS))} "
        f"are excused from having a prose surface but are not defect sources "
        f"any more. Delete the stale entries; an exemption for an id nothing "
        f"can file under excuses nothing."
    )
    unbound = []
    for source_id in sorted(vocab.DEFECT_SOURCE_IDS):
        covering = _surfaces_covering(source_id)
        if source_id in _SOURCELESS_FILING_IDS:
            assert not covering, (
                f"{source_id!r} is listed as having no prose surface -- "
                f"{_SOURCELESS_FILING_IDS[source_id]} -- but "
                f"{sorted(_rel(p) for p in covering)} now instructs a filing "
                f"under that name. Delete the exemption so the surface joins "
                f"the corpus and carries the rules."
            )
            continue
        if not covering:
            unbound.append(source_id)
    assert not unbound, (
        f"defect source(s) {unbound} resolve to no prose surface that "
        f"instructs a filing. A stream files under that `source` with nothing "
        f"telling it to set `tier`, `class` or `reproduction_attempted`, so "
        f"the doors refuse it mid-INSPECT. Either the surface exists and this "
        f"derivation cannot see it -- which is D-054 -- or it does not, and "
        f"the id belongs in _SOURCELESS_FILING_IDS with its reason."
    )


#: What EVERY filing surface owes, whatever register it writes in, located by
#: the tokens the DOORS themselves refuse on. Deliberately not the
#: word-identical stream rule: prove, trace and temper state the same
#: obligations in their own `## Key Constraints` voice, and demanding a paste
#: would be pinning a paraphrase of a document they do not carry.
#:
#: `class` is NOT on this floor and its absence is not an oversight. It is
#: pinned at the SHAPE for every surface that has one -- the `required` list of
#: the skills' ```json blocks (mirrored by schemas/findings.py) and the example
#: `defects` entries of the stream agents -- which is stronger than a token
#: sweep, because a shape is what a reader copies.
_FILING_SUBSTANCE = (
    ("`tier`", "names the evidence axis the filing doors require first"),
    ("LIVE", "names the tier for a finding the stream drove"),
    ("LATENT", "names the tier for a finding the stream only derived"),
    (
        "reproduction_attempted",
        "names the statement a LATENT filing is refused without (CT-001)",
    ),
    (
        "SECURITY_PROPERTY_CLAIM",
        "names the denylist class that refuses a LATENT security claim (CT-003)",
    ),
)

#: Filing surfaces KNOWN to be missing part of the floor, with the exact
#: clauses each lacks. Recorded rather than excused, and asserted as an EXACT
#: set so the debt cannot rot in either direction: closing a gap fails this
#: until the entry is deleted, and opening a new one fails immediately.
#:
#: Empty, and the emptiness is the assertion. skills/temper/SKILL.md was the
#: one entry: it stated the security rule as "A security-property claim can
#: NEVER be `LATENT`" without naming the denylist class the refusal reports, so
#: a temper filing that tripped it met a refusal naming a token its own prose
#: never taught. That gap closed when the file gained the full filing rules,
#: and the entry went with it -- the ledger is compared as an exact set below
#: precisely so a closed gap cannot keep its exemption.
_KNOWN_SUBSTANCE_GAPS: dict[str, frozenset[str]] = {}


def test_the_known_substance_gap_ledger_names_real_surfaces() -> None:
    """Floor check: a ledger keyed on a path nothing sweeps excuses nothing."""
    swept = {_rel(p) for p in DEFECT_FILING_SURFACES}
    stale = sorted(set(_KNOWN_SUBSTANCE_GAPS) - swept)
    assert not stale, (
        f"{stale} carry recorded substance gaps but are no longer filing "
        f"surfaces. Delete the entries -- a gap recorded against a file this "
        f"module does not read is a debt nothing will ever collect."
    )
    known_clauses = {clause for clause, _ in _FILING_SUBSTANCE}
    for rel, gaps in _KNOWN_SUBSTANCE_GAPS.items():
        unknown = sorted(gaps - known_clauses)
        assert not unknown, (
            f"{rel} is excused clauses {unknown} that are not on the substance "
            f"floor, so the exemption covers nothing the test would have "
            f"checked."
        )


@pytest.mark.parametrize("path", DEFECT_FILING_SURFACES, ids=_rel)
def test_every_filing_surface_carries_the_tier_substance(path: Path) -> None:
    """D-052 / D-053 / FR-004 / CT-001 / CT-003, over the corpus that files.

    The word-identical pins below reach the stream-report register only. This
    is the floor underneath them, and it is the assertion sight failed on every
    clause at once: its Step 4 instructed a `Foundry-Defect` call naming no
    tier, no class and no reproduction statement, and its own suggestion
    routing sent findings to a backlog on a work-effort grade. Nothing was red,
    because nothing swept the file.
    """
    text = _read(path)
    missing = frozenset(clause for clause, _ in _FILING_SUBSTANCE if clause not in text)
    known = _KNOWN_SUBSTANCE_GAPS.get(_rel(path), frozenset())
    why = dict(_FILING_SUBSTANCE)
    assert missing == known, {
        "file": _rel(path),
        "why": (
            "a surface that instructs a filing must state the substance the "
            "doors refuse on, in whatever register it writes in. Compared as "
            "an EXACT set against the recorded-gap ledger so a closed gap "
            "fails here too -- a stale exemption is how a fixed file goes "
            "unpinned again."
        ),
        "clauses_missing_and_not_recorded": {
            clause: why[clause] for clause in sorted(missing - known)
        },
        "clauses_recorded_but_now_present": sorted(known - missing),
    }


def test_sight_never_writes_the_defect_ledger_by_hand() -> None:
    """D-052: the one instruction that routes AROUND every door above.

    sight's Step 4 offered a second path -- "or write them directly to
    `foundry-archive/{run}/defects.json` if running outside an MCP session".
    A hand-written entry reaches the ledger without passing the tier check, the
    class check or the SECURITY_PROPERTY_CLAIM denylist, and is then
    indistinguishable there from a record that was refused nothing. Every rule
    this module pins is optional for as long as one surface documents a way
    past the door that enforces them.
    """
    flat = _flat(SIGHT_SKILL)
    assert "write them directly to" not in flat, (
        f"{_rel(SIGHT_SKILL)} again offers a direct write to the defect "
        f"ledger. There is no 'outside an MCP session' filing lane: without a "
        f"door there is no filing, only a report."
    )
    assert "Never write `foundry-archive/{run}/defects.json` yourself" in flat, (
        f"{_rel(SIGHT_SKILL)} no longer forbids hand-writing the defect "
        f"ledger. The prohibition is the pin, not the absence of the old "
        f"sentence -- prose that merely stops offering the bypass reads as "
        f"silence to the next author who needs one."
    )


def test_sight_routes_its_findings_by_tier_not_by_a_work_effort_grade() -> None:
    """D-053 / GI-001: two backlog mechanisms, one of them the abolished axis.

    sight shipped its own routing rule -- "Include minor UX suggestions as
    additional defects for GRIND. Append major UX suggestions to the backlog
    file." -- beside the tier rule every other stream carries. The axis that
    decides whether an instance is carried rather than fixed is `tier`: a
    LATENT finding stays open and reaches the F6 backlog. A second grade
    deciding the same question, in the words GI-001 bans by name, is the
    contradiction this asserts away.
    """
    flat = _flat(SIGHT_SKILL)
    for banned in ("minor UX suggestions", "major UX suggestions",
                   "Minor (auto-implement)", "Major (backlog)",
                   "Minor issues", "Major issues", "Critical issues"):
        assert banned not in flat, (
            f"{_rel(SIGHT_SKILL)} grades findings by work effort again "
            f"({banned!r}). GI-001 bans `minor`, `major` and `critical` BY "
            f"NAME on every filing surface, and sight is one -- it is a member "
            f"of vocab.DEFECT_SOURCE_IDS whose findings enter the same ledger."
        )
    assert "Nothing here routes on how large a fix looks" in flat, (
        f"{_rel(SIGHT_SKILL)} no longer states that nothing routes on the size "
        f"of a fix. Deleting the graded sentences is half the fix; the other "
        f"half is saying what replaced them, or the grade comes back the next "
        f"time someone needs to split a list in two."
    )


#: The two rules GI-001 puts in every stream agent, located by their bolded
#: openings. The first opens with BOTH historical spellings of the abolished
#: axis -- "No severity classification." (assayer, tracer, research-auditor,
#: coverage-diff) and "No severity tiers." (flow-tracer) -- because
#: UNWEAKENED_ABSOLUTES and ``test_coverage_diff_abolishes_its_severity_tier``
#: pin
#: each file's own spelling and one shared sentence has to satisfy all four
#: without any file losing an absolute it holds today. Carrying both also keeps
#: the ban findable by grep under either name.
_TIER_RULE_OPEN = "- **No severity classification.** **No severity tiers.**"
_TIER_RULE_CLOSE = 'no "I could not reproduce it, so it is probably fine."'


def _tier_rule(path: Path) -> str:
    """The two shared tier rules as one flattened span, or "" if absent."""
    flat = _flat(path)
    start = flat.find(_TIER_RULE_OPEN)
    if start == -1:
        return ""
    stop = flat.find(_TIER_RULE_CLOSE, start)
    return "" if stop == -1 else flat[start : stop + len(_TIER_RULE_CLOSE)]


def test_stream_agents_share_one_tier_rule_verbatim() -> None:
    """FR-030 + PATTERNS.md: one sentence, four files, byte-identical.

    A near-copy is the failure mode here, not an absent copy. Four
    hand-maintained paraphrases of one ruling drift a clause at a time, and
    the clause that goes first is whichever one the local voice found
    awkward -- which for this rule is the half that says the work-effort grade
    is still banned. Comparing the spans against each other (rather than each
    against a constant re-typed here) is what makes a divergence fail no
    matter which of the four was edited.
    """
    spans = {_rel(p): _tier_rule(p) for p in DEFECT_FILING_AGENTS}
    missing = sorted(rel for rel, span in spans.items() if not span)
    assert not missing, (
        f"{missing} carry no GI-001 tier rule at all. The rule opens "
        f"{_TIER_RULE_OPEN!r} and closes {_TIER_RULE_CLOSE!r} in every file "
        f"that files into the defect ledger; a file without it files findings "
        f"the doors refuse -- and one bad finding refuses the whole batch."
    )
    distinct = set(spans.values())
    assert len(distinct) == 1, {
        "why": (
            "the defect-filing agents no longer share ONE tier rule. "
            "patterns/PATTERNS.md rules a shared rule word-identical across "
            "all of them, and FR-030 pins the replacement wording. Fix the "
            "outlier rather than relaxing this assertion -- a per-file "
            "paraphrase is how one file quietly loses the clause banning the "
            "work-effort grade."
        ),
        "lengths_by_file": {rel: len(span) for rel, span in spans.items()},
    }


#: One claim per entry, each with the failure that claim's absence causes.
#: Parametrised over STREAM_AGENTS so a three-of-four edit fails.
_TIER_RULE_CLAUSES = (
    (
        _TIER_RULE_OPEN,
        "the rule lost one of the two bolded openings. Both spellings of the "
        "abolished axis are pinned by UNWEAKENED_ABSOLUTES across these four "
        "files, and both are what keep the ban findable however a future "
        "reader greps for it.",
    ),
    (
        "no `minor`, no `major`, no `critical`, no `severity`, no `priority`, no `impact`",
        "the rule no longer bans the work-effort grade BY NAME. FR-030 makes "
        "the wording free ONLY on that condition: a rule that introduces "
        "`tier` without naming what stays forbidden reads as the grade "
        "returning under a new label, which is exactly what D-041 removed.",
    ),
    (
        "Grade a finding by whether you actually drove it or only derived it from a "
        "scan, and never by how much work it would take to fix",
        "the rule no longer states BOTH halves of the distinction in ONE "
        "sentence. Split across two, a stream takes the new axis and leaves "
        "the prohibition, and the two axes end up coexisting instead of one "
        "replacing the other.",
    ),
    (
        "`tier` is evidence, not effort",
        "the rule no longer says what `tier` grades. Unstated, the closest "
        "available reading of a two-value ordered-looking field is severity.",
    ),
    (
        "`classification` still decides the channel a finding goes down and "
        "`target_kind` still rides on every filing",
        "the tier rule no longer reconciles itself with the channel rules "
        "below it. Read as a replacement rather than an addition, it retires "
        "`target_kind` -- and the comment-prose refusal reads that field and "
        "nothing else, so the split dies silently for every finding.",
    ),
    (
        "- **Set `tier` on every filing; the stream that files the defect is the one "
        "that sets it.**",
        "the rule making `tier` required on every filing is gone. FR-004 puts "
        "that duty on the FILING stream; unassigned, it lands on nobody and "
        "the doors refuse the finding.",
    ),
    (
        "schemas/vocab.py#DEFECT_TIERS",
        "the rule no longer cites the vocabulary module as the source of "
        "truth for the tier members, so the prose becomes a second copy free "
        "to drift from the set the doors validate against.",
    ),
    (
        "`LIVE` means you drove the door and observed the wrong result",
        "the rule no longer says what LIVE means, so a stream guesses -- and "
        "the guess a two-value field invites is 'the important one'.",
    ),
    (
        "`LATENT` means you derived the finding and found no reachable instance",
        "the rule no longer says what LATENT means.",
    ),
    (
        "MUST carry a `reproduction_attempted` statement naming what you drove and "
        "what it found",
        "the rule no longer demands the reproduction_attempted statement "
        "(FR-004 / CT-001). The server refuses a LATENT filing without one, so "
        "an agent file that omits it sends its stream into a refusal it has no "
        "instruction for.",
    ),
    (
        "`Foundry-Defect` and `Foundry-Sync` refuse a `LATENT` filing without one.",
        "the rule no longer states that the refusal is SERVER-SIDE. A "
        "requirement phrased as advice is one a stream talks itself out of at "
        "the moment it is inconvenient, which is the moment it matters.",
    ),
    (
        "A security-property claim can NEVER be `LATENT`",
        "the rule no longer rules out a LATENT security-property claim "
        "(CT-003). That is a denylist entry, not a judgement: 'I could not "
        "reproduce the auth bypass' is the single most dangerous sentence this "
        "vocabulary could let a stream write.",
    ),
    (
        "`SECURITY_PROPERTY_CLAIM`",
        "the rule no longer names the denylist class the refusal reports, so a "
        "stream that trips it cannot tell which rule it broke.",
    ),
    (
        "Both tiers are defects, both get fixed",
        "the rule no longer says both tiers are defects. A tier that excuses "
        "one of its values from being fixed IS the abolished axis.",
    ),
    (
        "buys you no discretion over anything else",
        "the rule lost its no-exceptions clause. Every rule in this register "
        "closes on one; without it `tier` reads as a licence rather than a "
        "field.",
    ),
)


@pytest.mark.parametrize("path", DEFECT_FILING_AGENTS, ids=lambda p: p.name)
@pytest.mark.parametrize("clause,why", _TIER_RULE_CLAUSES, ids=lambda v: v[:44])
def test_each_stream_agent_states_the_tier_rule(path: Path, clause: str, why: str) -> None:
    """GI-001 / AC-009 / FR-004 / CT-003, one claim at a time."""
    assert clause in _flat(path), f"{_rel(path)}: {why}"


#: FR-007 replaced an OPTIONAL class with a required one. The clauses that
#: permitted omission are gone from every file that carried one; these are the
#: sentences that replaced them. coverage-diff.md is the D-017 case and had no
#: class rule at all to soften -- it gained one, in its own voice, carrying
#: these same clauses.
_CLASS_REQUIRED_CLAUSES = (
    (
        "a single-instance class is still a class",
        "the file no longer tells a stream to name a class for a defect that "
        "stands alone. FR-007 makes `class` required on every filing; without "
        "this sentence a stream reads 'required' and 'only when shared' "
        "together and resolves the contradiction by omitting the field.",
    ),
    (
        "refuse a filing whose `class` is empty",
        "the file no longer states that an empty class is refused server-side. "
        "The path fallback survives only for READING pre-change archives, so a "
        "stream that omits the field is refused rather than defaulted.",
    ),
    (
        "spelled identically",
        "the file no longer requires the class string spelled identically "
        "across instances. Escalation counts a class by exact string, so an "
        "unpinned spelling reads as two unrelated classes and never escalates.",
    ),
)


@pytest.mark.parametrize("path", DEFECT_FILING_AGENTS, ids=lambda p: p.name)
@pytest.mark.parametrize("clause,why", _CLASS_REQUIRED_CLAUSES, ids=lambda v: v[:44])
def test_each_stream_agent_makes_the_class_required(path: Path, clause: str, why: str) -> None:
    """FR-007: agent prose and report formats REQUIRE the class."""
    assert clause in _flat(path), f"{_rel(path)}: {why}"


#: The class rule's bolded opening differs by stream voice -- "instances",
#: "packets", "deviations" -- so the bullet is located by the tail all four
#: share. ``test_each_stream_agent_instructs_the_class_declaration`` above pins
#: that tail, which is what makes it safe to key on here.
_CLASS_RULE_MARKER = "share a root cause.**"


def _class_rule(path: Path) -> str:
    """The one Rules bullet declaring the class, flattened and bounded.

    Bounded at the NEXT bullet, exactly as ``_rules_bullet`` is and for the
    same reason: "Omit the field" also appears in the neighbouring
    ``target_kind`` rule, where it is CORRECT prose about a different field. A
    file-wide absence assertion would fail on that sentence and invite the
    wrong fix.
    """
    flat = _flat(path)
    start = flat.find(_CLASS_RULE_MARKER)
    if start == -1:
        return ""
    head = flat.rfind("- **", 0, start)
    tail = flat[start:]
    stop = tail.find("- **")
    return flat[head if head != -1 else start : start + (len(tail) if stop == -1 else stop)]


@pytest.mark.parametrize("path", DEFECT_FILING_AGENTS, ids=lambda p: p.name)
def test_no_stream_agent_still_permits_omitting_the_class(path: Path) -> None:
    """FR-007's absence half: a removal is invisible to every positive pin.

    Four of the five carried a clause permitting omission -- assayer's "Omit
    the field when a defect genuinely stands alone", tracer's "Omit the field
    when a symbol's defect stands alone", flow-tracer's "Omit it when a packet
    fails alone", research-auditor's "Omit the field when a deviation stands
    alone"; coverage-diff.md named no class at all until D-017. A rule that
    says both "required" and "omit it when" is a rule read
    in the reader's favour, so the permission has to be gone, not merely
    outvoted. Scoped to the class BULLET: the identical words live in the
    target_kind rule two bullets down, where they are correct.
    """
    bullet = _class_rule(path)
    assert bullet, (
        f"{_rel(path)} has no class-declaration bullet to scope to -- the "
        f"marker {_CLASS_RULE_MARKER!r} is gone. Restore the rule rather than "
        f"deleting this assertion; FR-007 requires a producer in every stream."
    )
    for permission in ("Omit the field", "Omit it when", "is optional"):
        assert permission not in bullet, (
            f"{_rel(path)}'s class rule still permits omitting the field "
            f"({permission!r}). FR-007 makes `class` required on every filing; "
            f"a rule carrying both the requirement and the permission hands "
            f"the stream the choice back."
        )


@pytest.mark.parametrize("path", DEFECT_FILING_AGENTS, ids=lambda p: p.name)
def test_no_stream_agent_shape_still_calls_the_class_optional(path: Path) -> None:
    """FR-007 at the SHAPE, where the rule's reader actually copies from.

    Every one of the four qualified its example JSON with "`class` is optional
    and appears only ..."; coverage-diff.md's shape carried neither field.
    D-016's finding is that a shape and a mandate which
    disagree are resolved in the shape's favour, because the shape is the
    thing that gets pasted -- so the prose beside the example has to change
    with the rule, not after it.
    """
    flat = _flat(path)
    assert "`class` is optional" not in flat, (
        f"{_rel(path)} still describes `class` as optional beside its example "
        f"JSON. FR-007 made it required on every filing; a shape annotated "
        f"'optional' outranks a Rules bullet that says otherwise (D-016)."
    )
    assert "`class` is required on every" in flat, (
        f"{_rel(path)} does not state at the SHAPE that `class` is required on "
        f"every record. The Rules block alone is not enough -- the reader who "
        f"copies the shape may never reach it."
    )
    assert "`tier` is required on every" in flat, (
        f"{_rel(path)} does not state at the SHAPE that `tier` is required on "
        f"every record either (FR-004), so the new axis is documented only in "
        f"a rule the shape-copier may never read."
    )


def _example_defect_records(path: Path) -> list[dict]:
    """Every entry of every `defects` array in a file's normative examples.

    Scoped to `defects` rather than to every nested object (which is what
    ``_example_records`` returns) because the tier and class obligations are
    obligations on a DEFECT record. A `results` or `recommendations` entry
    carrying no tier is correct, and sweeping those in would force the pin to
    be weakened to "at least one record has a tier" -- which passes on a shape
    that carries it once and omits it everywhere else.
    """
    found: list[dict] = []
    for record in _example_records(path):
        entries = record.get("defects")
        if isinstance(entries, list):
            found.extend(e for e in entries if isinstance(e, dict))
    return found


@pytest.mark.parametrize("path", DEFECT_FILING_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_report_shape_carries_tier_on_every_defect(path: Path) -> None:
    """FR-004 / FR-007: the instruction is inert if the shape has no slot.

    D-011's shape exactly, at the new axis. The Rules block can require `tier`
    on every filing and a stream will still copy the example beside it -- the
    example is the thing that gets pasted. Read out of the files' own JSON so
    a shape that gains a defect entry later is covered without anyone
    remembering to come back here.
    """
    records = _example_defect_records(path)
    assert records, (
        f"{_rel(path)}'s normative example has no `defects` array entries, so "
        f"every assertion below it is vacuous. The array is what the lead "
        f"converts into GRIND tasks; a shape without one documents nothing."
    )
    for entry in records:
        assert entry.get("tier") in vocab.DEFECT_TIERS, (
            f"{_rel(path)} documents a defect entry whose `tier` is "
            f"{entry.get('tier')!r}, not a member of "
            f"{sorted(vocab.DEFECT_TIERS)}. A stream copying this shape files "
            f"a finding the door refuses, and CT-001's refusal names the tier "
            f"field the example never taught it to set."
        )
        assert str(entry.get("class", "")).strip(), (
            f"{_rel(path)} documents a defect entry with no non-empty `class` "
            f"(FR-007). `class` is required on every filing now, so an example "
            f"entry without one teaches the omission the doors refuse."
        )


@pytest.mark.parametrize("path", DEFECT_FILING_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_shape_works_a_latent_entry(path: Path) -> None:
    """CT-001: the LATENT half needs a worked example, not just a rule.

    LIVE is the shape every existing example already had. LATENT is the new
    one, and it is the one that carries an extra required field -- so it is
    the one a stream gets wrong. An example set that shows only LIVE entries
    documents the easy half of the vocabulary and leaves the refusal to be
    discovered at the door.
    """
    latent = [e for e in _example_defect_records(path) if e.get("tier") == "LATENT"]
    assert latent, (
        f"{_rel(path)}'s example `defects` array works no LATENT entry. The "
        f"shape then teaches only the tier that needs no extra field, and a "
        f"stream filing its first LATENT finding meets CT-001's refusal with "
        f"no worked example to copy."
    )
    for entry in latent:
        statement = entry.get("reproduction_attempted")
        assert vocab.reproduction_attempted_problem(statement) is None, (
            f"{_rel(path)} documents a LATENT defect entry whose "
            f"`reproduction_attempted` is {statement!r}, which the SHIPPED "
            f"check rejects: {vocab.reproduction_attempted_problem(statement)}. "
            f"The example is driven through vocab rather than eyeballed, so a "
            f"placeholder ('n/a', 'TBD') in the shape fails here instead of at "
            f"the door."
        )


# ---------------------------------------------------------------------------
# D-174 (filing-type half) / FR-004 / CT-002 -- the `type` a surface publishes
# is a type the door accepts
# ---------------------------------------------------------------------------
#
# The tier and class rungs above sweep every published `defects` entry, and
# `validate_defect_filing` -- the shared validator both doors call, and the
# function `test_every_documented_latent_example_survives_the_filing_door`
# drives the examples through -- reads `tier` and `class` and NOT `type`. The
# `type` rung lives one layer out, in `tools/foundry.py`:
#
#     if defect_type not in DEFECT_TYPES:
#         ... "Use the closest member of the canonical set"
#
# So a published example could carry a `type` no door would ever accept and
# every check in this module stayed green. Four did, across three surfaces:
#
#     agents/coverage-diff.md   "type": "MISSING_COVERAGE_LIST"
#     agents/flow-tracer.md     "type": "DISCONNECTED", "type": "CHAIN_BROKEN"
#     agents/tracer.md          `type: "SERENA_UNAVAILABLE"`  (a Rules bullet)
#     agents/flow-tracer.md     `type: "SERENA_UNAVAILABLE"`  (a Rules bullet)
#
# Driven: a stream that copies its own instructions' shape meets
# `Foundry-Sync` refusing the WHOLE batch, naming a `type` those instructions
# taught it. The last two are worse than an example, because they sit under
# `**`NOT_VERIFIED` is a defect, not a deferral.**` -- an absolute promising
# the filing is never waived and never omitted, whose one prescribed `type`
# the door always refused, at the moment Serena was already down and that
# filing was the only record of it.
#
# Each surface keeps its OWN verdict vocabulary (flow-tracer's `SOURCED`/
# `CHAIN_BROKEN` summary counts, coverage-diff's `ORPHAN_DESTINATION`): those
# are a different axis and nothing files them. `vocab.py`'s own DEFECT_TYPES
# comment sets the precedent -- tracer.md keeps `MISPLACED` as its verdict
# word while persisting `type: "ARCHITECTURAL_PLACEMENT"`. What is pinned here
# is only the value that rides the wire.

#: A `type` field set to an UPPER_SNAKE literal, anywhere in a surface --
#: inside a normative JSON example or inline in a Rules bullet, quoted as
#: `"type"` or bare as `type`. The case class is what separates a defect type
#: from a JSON-Schema one: `"type": "string"` / `"array"` / `"object"` in the
#: skills' findings schemas are lower case and never match, so the schema
#: blocks need no exclusion list that could rot. The lookbehind is what keeps
#: `"spec_type": "MIGRATION"` out -- without it the pattern matches the tail
#: of any `*_type` key and reports a spec type as a bad defect type.
_TYPE_LITERAL_RE = re.compile(r'(?<![A-Za-z0-9_])"?type"?\s*:\s*"([A-Z][A-Z0-9_]*)"')


def test_the_published_type_sweep_is_not_vacuous() -> None:
    """Floor check: a regex that matches nothing pins nothing.

    The sweep below passes trivially on a corpus where no surface publishes a
    `type` literal at all -- which is also what a broken regex looks like.
    This is the floor that fails first, and it names the members the corpus is
    known to carry so a pattern that silently stopped matching UPPER_SNAKE
    values cannot be mistaken for a clean corpus.
    """
    found: set[str] = set()
    for path in DEFECT_FILING_SURFACES:
        found.update(_TYPE_LITERAL_RE.findall(_read(path)))
    assert found, (
        "no surface in DEFECT_FILING_SURFACES publishes a `type` literal any "
        "more. Either every example lost its `type` -- which is itself the "
        "defect, since `type` is required on the wire -- or _TYPE_LITERAL_RE "
        "stopped matching. Fix whichever it is; never delete this floor."
    )
    assert "MISSING" in found, (
        f"the corpus publishes {sorted(found)} and not the single most common "
        f"defect type. That is a regex regression, not a corpus change."
    )


@pytest.mark.parametrize("path", DEFECT_FILING_SURFACES, ids=_rel)
def test_every_published_type_is_a_vocabulary_member(path: Path) -> None:
    """D-174: the spelling a surface teaches is one the door accepts.

    Swept over DEFECT_FILING_SURFACES rather than DEFECT_FILING_AGENTS, and
    over the RAW text rather than over `_example_defect_records`, because two
    of the four originals were not in a `defects` array at all -- they were
    inline in a Rules bullet that names the `type` to file. A sweep scoped to
    parsed JSON would have found half the class and called it fixed.

    Compared against ``vocab.DEFECT_TYPES`` read from the module, never a list
    re-typed here: a member added by RFC is accepted the moment the vocabulary
    accepts it, and the bug and its check cannot end up on the same side of
    one edit.
    """
    published = sorted(set(_TYPE_LITERAL_RE.findall(_read(path))))
    refused = [t for t in published if t not in vocab.DEFECT_TYPES]
    assert not refused, (
        f"{_rel(path)} teaches its stream to file {refused} as a `type`, and "
        f"none is a member of vocab.DEFECT_TYPES "
        f"({sorted(vocab.DEFECT_TYPES)}). `tools/foundry.py`'s rung refuses "
        f"that filing -- and at the batch door it refuses the WHOLE batch, so "
        f"one copied example discards every other finding of the cycle. The "
        f"stream's own verdict words are a different axis and belong in "
        f"`verdict`, in `summary` and in prose; only the value on the wire is "
        f"swept here. Fix the SURFACE (or extend the vocabulary by RFC), "
        f"never this assertion."
    )


# ---------------------------------------------------------------------------
# FR-019 / FR-040 -- pointer dispatch, teammate side
# ---------------------------------------------------------------------------


def test_teammate_has_the_pointer_dispatch_step() -> None:
    """FR-019: the teammate reads the FILE and states the hash it read.

    The spawn doors stopped returning the prompt text and started returning a
    pointer. That is only half a contract: a teammate handed a path and a hash
    can still work from the dispatch message's own summary and never open the
    file. The hash in the completion report is what closes it -- only an agent
    that read the file can produce the value -- and this pins the instruction
    that produces it.
    """
    flat = _flat(TEAMMATE)
    assert "### Step 0: Read your prompt FILE in full, and report the hash you read" in flat, (
        "teammate.md has no pointer-dispatch step. Dispatch hands the teammate "
        "a path and a hash instead of the prompt text (FR-019); with no step "
        "telling it to open the file, the pointer is a message it can skim."
    )
    assert "Read that file end to end before any other action" in flat, (
        "teammate.md no longer requires the prompt file be read IN FULL before "
        "anything else. FR-019 names all three: the path, the hash, and the "
        "instruction to read the whole file."
    )
    assert "hashlib.sha256" in _read(TEAMMATE), (
        "teammate.md gives no command for computing the hash itself. A "
        "teammate told to state a hash and given no way to derive one copies "
        "it out of the dispatch message, which is precisely the case the "
        "check exists to detect."
    )


def test_teammate_completion_report_requires_the_prompt_hash() -> None:
    """FR-019: the report is where the lead picks the value up.

    ``check_reported_prompt_hash`` is called at Foundry-Accept-Casting and at
    Foundry-Fix with the hash the LEAD passes through, and the lead has
    nothing to pass unless the completion report carries it. An instruction to
    compute a hash with nowhere to put it is an instruction with no consumer.
    """
    flat = _flat(TEAMMATE)
    assert "**The prompt hash you read (required).**" in flat, (
        "teammate.md's completion-message list no longer requires the prompt "
        "hash (FR-019). Without that bullet the lead reaches "
        "Foundry-Accept-Casting with no value to pass and the rung never fires."
    )
    for tool in ("`Foundry-Accept-Casting`", "`Foundry-Fix`"):
        assert tool in flat, (
            f"teammate.md does not bind the reported hash to {tool}, so the "
            f"teammate is not told what refuses when the value is wrong."
        )
    assert "Never work from a summary of your prompt" in flat, (
        "teammate.md lost the prohibition on working from a summary or from a "
        "prompt quoted back in a message. The pointer only binds if the file "
        "is the sole authority; a quote is a copy free to differ from what the "
        "gate hashes."
    )
    # The pre-existing citation bullet must survive beside the new one:
    # test_symbol_cites.py binds the acceptance window to this exact clause.
    assert "within 300 characters of the ID mention" in flat, (
        "teammate.md's requirement-citation bullet lost the 300-character "
        "clause while the prompt-hash bullet was added beside it. "
        "test_symbol_cites.py binds the acceptance gate's window to that "
        "sentence -- D-118 exists because the prose and the window disagreed."
    )


# ---------------------------------------------------------------------------
# FR-008 / FR-041 -- the LATENT fix lane, beside the LIVE one
# ---------------------------------------------------------------------------


def test_teammate_grind_protocol_has_the_latent_lane() -> None:
    """FR-008: a LATENT fix closes on a regression_test locator.

    A LATENT defect has no adjacent path the fix could have broken, because no
    path reached the code at all. Demanding the LIVE lane's adjacent-path
    statement of it produces a teammate inventing neighbours to satisfy a
    gate, in the phase where every defect must close -- which is worse than no
    declaration.
    """
    flat = _flat(TEAMMATE)
    assert (
        "### Step 7, second lane: DECLARE — the regression test that closes a "
        "LATENT defect" in flat
    ), (
        "teammate.md's GRIND protocol has no LATENT lane (FR-008). Without it "
        "a teammate fixing a derived defect is held to an adjacent-path "
        "statement about code nothing reaches."
    )
    assert "`path::test`" in flat, (
        "teammate.md does not give the regression_test locator's FORM. "
        "`Foundry-Fix` validates `path::test` -- that the path exists and the "
        "test names a real test in it -- so a locator in any other shape is "
        "refused."
    )
    assert "`authored_by`" in flat, (
        "teammate.md does not name `authored_by`, which Foundry-Fix requires "
        "on every fix in either lane (CT-005)."
    )


def test_teammate_keeps_the_failing_then_passing_account_out_of_the_call() -> None:
    """FR-041: the statement is REPORT prose, never a tool argument.

    CT-004 is explicit that the failing-then-passing statement is completion-
    report prose and not an input, and the account is worth writing down
    because only it proves the test was ever red.

    WHY THE REASON IS PINNED TOO (D-005 / D-010)
    --------------------------------------------
    The instruction shipped with a justification that was not true: it told
    the teammate the call would be "rejected at the MCP boundary before it
    reaches a handler" because "the schema declares no field for it". Driven:
    `additionalProperties` occurs ZERO times in server.py, so Foundry-Fix's
    published inputSchema does not close its property set; JSON Schema permits
    extra properties by default; and the MCP SDK's boundary check is exactly
    jsonschema.validate against that schema, which ACCEPTS the undeclared key.
    The dispatch lambda then reads only its named arguments, so the account is
    silently discarded and the call reports success.

    That is the same defect shape FR-025 removes from the temper skill -- a
    document citing a guard that does not exist. The instruction was right and
    the reason was wrong, which is the worse combination: an agent that tests
    the stated reason and finds it false has been given cause to doubt the
    rule. So both halves are pinned here.
    """
    flat = _flat(TEAMMATE)
    assert "**The failing-then-passing account is not a tool argument.**" in flat, (
        "teammate.md no longer rules the failing-then-passing account out of "
        "the Foundry-Fix call (FR-041 / CT-004)."
    )
    assert "the test failed at" in flat and "and passes at" in flat, (
        "teammate.md gives no shape for the failing-then-passing account, so "
        "the requirement is satisfiable by any sentence mentioning a test. "
        "FR-041 requires the STATEMENT in the report."
    )
    assert "rejected at the MCP boundary" not in flat, (
        "teammate.md claims an undeclared Foundry-Fix argument is rejected at "
        "the MCP boundary. It is not: the tool's inputSchema sets no "
        "`additionalProperties: false` (grep server.py -- zero occurrences), "
        "so the boundary's jsonschema.validate accepts the key. Justify the "
        "rule by what actually happens, or the first teammate to check the "
        "reason stops trusting the rule."
    )
    assert "it is DROPPED" in flat, (
        "teammate.md no longer says what actually happens to an undeclared "
        "Foundry-Fix argument. It is dropped, not refused -- the dispatch "
        "reads only the arguments the schema names -- and silent loss is "
        "exactly why the account has to go in the report instead."
    )


def test_teammate_latent_lane_does_not_relax_the_live_lane() -> None:
    """FR-008: the second lane is beside the first, never in place of it.

    The LIVE lane's adjacent-path statement and test stay mandatory. A LATENT
    lane written as a relaxation is a lane every fixer takes, and the
    adjacent-path discipline the D-070 work installed evaporates one defect at
    a time.
    """
    flat = _flat(TEAMMATE)
    assert "**This lane does not relax the one above it.**" in flat, (
        "teammate.md does not state that the LATENT lane leaves the LIVE lane "
        "untouched. Two lanes with no boundary between them is one lane, and "
        "it is the cheaper one."
    )
    assert "Read the defect's `tier` first and pick the lane it names" in flat, (
        "teammate.md does not tell the fixer WHICH lane a defect takes. The "
        "tier decides it; unstated, the fixer picks, and picks the shorter one."
    )
    # The LIVE lane's own pins must survive the addition.
    assert "**The adjacent-path statement.**" in flat, (
        "teammate.md lost the LIVE lane's adjacent-path statement while the "
        "LATENT lane was added beside it (FR-009)."
    )


def test_teammate_filing_a_defect_sets_tier_and_class() -> None:
    """FR-004 / FR-007: a teammate files defects too, under the same rules."""
    flat = _flat(TEAMMATE)
    assert "**When you FILE a defect rather than fix one,**" in flat, (
        "teammate.md never tells a teammate what to put on a defect it FILES. "
        "The filing doors require `tier` and `class` from every caller, not "
        "only from the four INSPECT streams."
    )
    for token in ("`tier`", "`class`", "`reproduction_attempted`", "`SECURITY_PROPERTY_CLAIM`"):
        assert token in flat, (
            f"teammate.md's filing rule does not name {token}, so a teammate "
            f"meets that refusal with no instruction covering it."
        )


# ---------------------------------------------------------------------------
# GI-005 / FR-043 -- what a teammate does NOT report, and what it commits
# ---------------------------------------------------------------------------


_SPEND_HEADING = "### NEVER report token counts, durations, or cost"


def _teammate_section(heading: str) -> str:
    """One `###` section of teammate.md, flattened and bounded at the next one.

    Bounded for the reason ``_rules_bullet`` is: a file-wide pin on a tool
    name is satisfiable from anywhere in an 890-line document, and the point
    of naming `Foundry-Spend` in this rule is that THIS RULE states the
    mechanism -- not that the string exists somewhere in the file.
    """
    flat = _flat(TEAMMATE)
    start = flat.find(heading)
    if start == -1:
        return ""
    tail = flat[start + len(heading) :]
    stop = tail.find("### ")
    return heading + (tail if stop == -1 else tail[:stop])


def test_teammate_never_reports_spend() -> None:
    """GI-005: the parser stays out of the run; the lead reads the block.

    The usage block is fragile, human-facing text nothing in this system
    owns. A parser for it inside the run breaks silently on the next harness
    release and then reports a WRONG number rather than no number, which is
    the worse of the two failures. One reader, on the lead's side, is the
    whole ruling.

    The rule has to NAME the tool that reader uses. A prohibition that says
    only "the lead records spend" is the D-020 / D-061 / D-072 shape this
    module keeps paying for: a working mechanism with no documented caller.
    `Foundry-Spend` exists, the lead is the one who calls it, and a teammate
    that knows neither fact has no way to tell "somebody else records this"
    from "nobody records this" -- and the second reading is the one that ends
    with a teammate helpfully estimating a number.
    ``test_every_tool_named_in_shipped_prose_is_registered`` covers the other
    half, that the name is one the server actually serves.
    """
    section = _teammate_section(_SPEND_HEADING)
    assert section, (
        f"teammate.md has no {_SPEND_HEADING!r} section, so nothing forbids a "
        f"teammate reporting its own spend (GI-005). Unstated, a diligent "
        f"teammate estimates one -- and an estimate in the ledger is "
        f"indistinguishable from a measurement."
    )
    assert "`Foundry-Spend`" in section, (
        "teammate.md's spend rule no longer names `Foundry-Spend` as the tool "
        "the lead records through. Naming the actor without the mechanism "
        "leaves the teammate unable to distinguish 'someone else records this' "
        "from 'nobody records this', and only one of those readings ends with "
        "the teammate leaving the number alone."
    )
    assert "never yours to call" in section, (
        "teammate.md's spend rule names `Foundry-Spend` without ruling it out "
        "of the teammate's own hands. A tool named in a teammate's protocol "
        "reads as a tool the teammate may call; this one is the lead's."
    )
    assert "Spend is not yours to report." in section, (
        "teammate.md's spend rule lost its closing absolute."
    )


def test_teammate_commits_its_own_evidence_logs() -> None:
    """FR-043: teammates commit per-casting logs; nobody sweeps by hand.

    A hand sweep at the end of the run is the shape GI-002 removes on the
    server side; this is its teammate-side twin. A log a teammate meant to
    commit and did not is not a log the lead finds later -- it is a
    requirement with no evidence bound to it, and the acceptance gate names it
    as one.
    """
    flat = _flat(TEAMMATE)
    assert (
        "**You commit your own casting's evidence logs, in your own "
        "pathspec-scoped commit. Nobody sweeps `evidence/` by hand.**" in flat
    ), (
        "teammate.md does not state that the teammate commits its own evidence "
        "logs and that nothing sweeps them by hand (FR-043). The server "
        "re-executes what was COMMITTED; an uncommitted log is invisible to it."
    )


# ---------------------------------------------------------------------------
# GRIND cycle 5 -- D-092 / D-093 / D-096: the survivors a phrase list misses
# ---------------------------------------------------------------------------
#
# All three defects were graded prose sitting in a file that ALSO bans the
# grade, and all three passed 839 tests. The pins that should have caught them
# could not: ``_APPROVING_SEVERITY_USES`` is a hand-typed tuple of comparative
# shapes, and "HOLLOW verdicts are highest priority", "Critical path gaps" and
# "Do NOT flag cosmetic/style issues" are none of them. A fourth survivor would
# not be on that tuple either.
#
# So the sweep below is derived on every axis it can be. The token set is read
# out of the ban clause itself, so a seventh banned spelling added to the rule
# is swept the moment it lands. The corpus is DEFECT_FILING_SURFACES, so a new
# filing surface is swept the moment it names a door. Only the innocent uses
# are hand-listed -- as an EXACT set, so a new one fails until somebody writes
# down why it is the English word rather than the grade, in a diff a reviewer
# reads. That written act is the thing that never happened for "highest
# priority", and it is the whole point of recording rather than excusing.

#: The banned work-effort spellings, READ from the shared ban clause rather
#: than re-typed here -- the ``_EXPECTED_TYPE_ENUM`` discipline applied to
#: prose. The clause names each one as a code span behind the word "no", which
#: is a shape a USE never takes: a document reaching for the axis writes "high
#: severity" or "highest priority", never "no `severity`".
_BANNED_GRADE_RE = re.compile(r"no `([a-z]+)`")

#: The other shape a banned spelling only ever takes when it is being RULED
#: OUT: "a channel, not a severity tier", "a channel statement, not a severity
#: one". A document reaching FOR the axis never phrases it as a denial, so this
#: is derived rather than recorded per file -- which matters because the same
#: denial sentence is shared prose that lands in a surface the moment it joins
#: the corpus, and a ledger entry for it would be a debt owed to whichever
#: casting happened to commit first.
_AXIS_DENIAL_RE_TEMPLATE = r"(?:not|never) an? (?:%s)"


def _banned_grade_tokens() -> frozenset[str]:
    """The spellings the shared rule bans, parsed out of the rule."""
    return frozenset(_BANNED_GRADE_RE.findall(_tier_rule(ASSAYER)))


def test_the_banned_grade_tokens_derive_from_the_ban_clause() -> None:
    """Floor check: a derivation that silently returns {} sweeps nothing.

    Every assertion below is parametrised on this set. If the regex stops
    matching -- the rule reflows, the backticks go, the enumeration moves to a
    table -- the sweeps do not fail, they go vacuous, and a vacuous guard is
    how D-092 lived through four GRIND cycles. This is the assertion that
    fails instead.
    """
    tokens = _banned_grade_tokens()
    assert "severity" in tokens, (
        f"the ban-clause derivation found {sorted(tokens)} and not the "
        f"historical name of the axis. agents/assayer.md's shared rule is the "
        f"source; either it stopped enumerating the banned spellings as "
        f"`no \\`x\\`` code spans -- which is itself the FR-030 violation, "
        f"because the enumeration is what makes the ban a ban -- or "
        f"_BANNED_GRADE_RE no longer matches the shape it takes. Fix whichever "
        f"moved; do not re-type the members here."
    )
    assert len(tokens) >= 6, (
        f"the ban clause now names only {sorted(tokens)}. FR-030 requires the "
        f"work-effort grade to stay banned BY NAME, and a shortened "
        f"enumeration narrows every sweep below it in one edit."
    )


#: Occurrences of a banned spelling that are the ORDINARY ENGLISH WORD, or a
#: file's own prohibition of the axis, recorded one by one with the reason.
#: Asserted as an EXACT set in both directions: a new occurrence fails until it
#: is deleted or recorded, and a recorded phrase that no longer occurs fails
#: too, so a stale entry cannot quietly re-open the hole it once described.
#:
#: The shared no-severity rule is NOT listed. It is excised mechanically by
#: ``_tier_rule`` before this ledger is consulted, because it is the same 1924
#: characters in every file that carries it and pasting it here six times
#: would be a second copy free to drift from the one the rule pins.
_RECORDED_GRADE_WORD_USES: dict[str, dict[str, str]] = {
    "plugins/foundry/agents/assayer.md": {
        "This ordering is critical": (
            "spec-before-code ordering, an adjective on a METHOD step. Nothing "
            "here grades a finding."
        ),
        'Never say "minor issue" or "small gap."': (
            "the No-softening rule banning the word by quoting it. Deleting "
            "the quotation would delete the prohibition."
        ),
        "The word \"minor\" doesn't exist in your vocabulary.": (
            "the same rule's close, naming the word it abolishes."
        ),
    },
    "plugins/foundry/agents/teammate.md": {
        "Auto-add missing critical functionality": (
            "RULE 2's heading. `critical` qualifies functionality every "
            "production build needs -- validation, auth, error handling -- "
            "not the importance of a defect."
        ),
        "making a major schema migration": (
            "the SIZE of an architectural change, in RULE 4's examples of what "
            "a teammate escalates rather than does."
        ),
        "- **Impact:** [what breaks or is suboptimal without the architectural change]": (
            "a concerns.md template field asking WHAT BREAKS. It records a "
            "consequence, and the lead reads it to decide whether to "
            "re-decompose -- it ranks nothing against anything."
        ),
        "Do not upgrade major versions of build dependencies": (
            "semver. `major` here is a version component."
        ),
        "add critical functionality, fix blockers": (
            "the SUMMARY section restating RULE 2, same sense as its heading."
        ),
    },
    "plugins/foundry/skills/prove/SKILL.md": {
        "For each major feature, enumerate reasonable scenarios": (
            "`major` = principal, scoping which features get scenario "
            "expansion. It grades no finding; every finding the expansion "
            "produces is a defect on the same terms."
        ),
        "what code does, user impact, files, fix direction": (
            "a Findings report field naming what a user hits. Describing a "
            "consequence is what a defect record is FOR; the abolished axis "
            "was a judgement about whether the consequence was worth fixing."
        ),
        "there is no off-the-critical-path exemption": (
            "the FAIL rule closing that exemption BY NAME (D-041, pinned by "
            "test_prove_skill_closed_the_critical_path_exemption)."
        ),
        "that was the severity axis wearing a different name": (
            "the same sentence naming the axis it abolishes."
        ),
    },
    "plugins/foundry/skills/sight/SKILL.md": {
        "**CRITICAL: This skill requires the Playwright MCP browser tools": (
            "a setup PRECONDITION. Without the browser tools the skill cannot "
            "run at all; this is not a finding and has no tier."
        ),
        "- **User impact:** {what breaks": (
            "a console-error report template field. Same reading as prove's."
        ),
        "- **Impact**: {what user-facing behavior this causes}": (
            "a network-failure report template field."
        ),
        "- **Impact**: {what breaks}": (
            "a request-failure report template field."
        ),
    },
    "plugins/foundry/skills/trace/SKILL.md": {
        "For each major feature, walk through the complete workflow": (
            "`major` = principal, scoping the plumber check."
        ),
        "What spec says vs code does, impact.": (
            "the DEV-N report field naming what the deviation causes."
        ),
    },
}


def _grade_word_scan_text(path: Path) -> str:
    """A surface's prose with every EXCEPTED occurrence blanked out.

    Three exceptions are mechanical -- the shared no-severity rule, the
    ``no `x``` enumeration form each file bans the axis in, and the "not a
    `x`" denial form -- and the fourth is the recorded ledger above. What
    survives all four is a banned spelling this module has never been told
    about.

    The placeholders substituted in carry no banned spelling of their own. The
    first draft blanked the shared rule out as ``<shared no-severity rule>``
    and the sweep then matched its own marker in all six files that carry the
    rule -- a guard failing on the evidence it just erased.
    """
    flat = _flat(path)
    shared = _tier_rule(path)
    if shared:
        flat = flat.replace(shared, " <the shared rule> ")
    flat = _BANNED_GRADE_RE.sub(" <ban> ", flat)
    flat = re.sub(
        _AXIS_DENIAL_RE_TEMPLATE % "|".join(sorted(_banned_grade_tokens())),
        " <denied> ",
        flat,
        flags=re.I,
    )
    for phrase in _RECORDED_GRADE_WORD_USES.get(_rel(path), {}):
        flat = flat.replace(phrase, " <recorded> ")
    return flat


@pytest.mark.parametrize("path", DEFECT_FILING_SURFACES, ids=_rel)
def test_no_filing_surface_grades_a_finding_by_work_effort(path: Path) -> None:
    """D-092 / D-096 / GI-001 / FR-030, over the corpus that files.

    prove/SKILL.md ranked its own verdicts -- "HOLLOW verdicts are highest
    priority" -- one screen from the paragraph banning `priority` by name, and
    asked its assayer for two more gradings besides ("Critical path gaps",
    "quality confidence (HIGH/MEDIUM/LOW)") in a file whose FAIL rule says
    there is "no off-the-critical-path exemption, because that was the
    severity axis wearing a different name". A reader resolves a document that
    contradicts itself in its own favour, so the file abolished the axis and
    reinstated it in the same read.
    """
    rx = re.compile(
        r"(?<![A-Za-z])(%s)(?![A-Za-z])" % "|".join(sorted(_banned_grade_tokens())),
        re.I,
    )
    text = _grade_word_scan_text(path)
    found = [
        text[max(0, m.start() - 70) : m.end() + 70] for m in rx.finditer(text)
    ]
    assert not found, {
        "file": _rel(path),
        "why": (
            "a banned work-effort spelling appears outside this file's own ban "
            "clause and outside the recorded-use ledger. Two readings, and the "
            "fix differs: if the word GRADES a finding -- ranks one above "
            "another, exempts one from being filed, or asks how much a fix is "
            "worth -- delete it, because GI-001 abolished that axis and `tier` "
            "records evidence instead. If it is the ordinary English word on "
            "something that is not a finding, add it to "
            "_RECORDED_GRADE_WORD_USES with the reason. Recording is not a way "
            "past this assertion; it is the reviewable act D-092 never had."
        ),
        "banned_spellings": sorted(_banned_grade_tokens()),
        "unrecorded_uses": found,
    }


def test_the_recorded_grade_word_ledger_is_exact() -> None:
    """A stale exemption re-opens the hole it was written to describe.

    An entry that no longer matches excuses nothing and hides that the sweep
    above has gone one occurrence narrower than its author believed. Both
    directions fail here so the ledger tracks the prose rather than outliving
    it.
    """
    swept = {_rel(p) for p in DEFECT_FILING_SURFACES}
    stale_files = sorted(set(_RECORDED_GRADE_WORD_USES) - swept)
    assert not stale_files, (
        f"{stale_files} carry recorded grade-word uses but are no longer "
        f"filing surfaces. Delete the entries -- an exemption against a file "
        f"this module does not read excuses nothing."
    )
    for rel, uses in _RECORDED_GRADE_WORD_USES.items():
        path = FOUNDRY_ROOT.parent.parent / rel
        flat = _flat(path)
        shared = _tier_rule(path)
        if shared:
            flat = flat.replace(shared, " <the shared rule> ")
        gone = sorted(phrase for phrase in uses if phrase not in flat)
        assert not gone, (
            f"{rel} no longer contains recorded use(s) {gone}. Either the "
            f"prose was rewritten -- in which case delete the entry, the "
            f"sweep covers the replacement now -- or the phrase drifted and "
            f"the entry is excusing an occurrence nobody has read."
        )


#: Phrasings that hand a reader DISCRETION over a finding: rank it, hold it
#: back, or defer it. None of them appears in any ban clause in the corpus --
#: a rule forbidding the axis says "no exceptions, no deferrals", never "safe
#: to skip" -- so these need no exception layer at all.
#:
#: trace/SKILL.md closed its `## Key Constraints` list with "Do NOT flag
#: cosmetic/style issues -- only structural completeness gaps" while eight
#: sibling surfaces closed the same rule with a no-discretion sentence. A
#: stream told to withhold findings by how much they matter is running the
#: severity axis under another name, in the release that abolished it.
_DISCRETION_EXEMPTIONS = (
    ("highest priority", "ranks one finding above the rest"),
    ("top priority", "ranks one finding above the rest"),
    ("higher priority", "ranks one finding against another"),
    ("lower priority", "ranks one finding against another"),
    ("priority order", "states a fix order the abolished axis decided"),
    ("cosmetic/style", "withholds a finding for being slight"),
    ("purely cosmetic", "withholds a finding for being slight"),
    ("merely cosmetic", "withholds a finding for being slight"),
    ("only cosmetic issues", "withholds a finding for being slight"),
    ("safe to skip", "exempts a finding from being filed"),
    ("can be skipped", "exempts a finding from being filed"),
    ("can be deferred", "defers a finding the run refuses to defer"),
    ("nice to have", "grades a finding as optional"),
    ("nice-to-have", "grades a finding as optional"),
)


@pytest.mark.parametrize("path", DEFECT_FILING_SURFACES, ids=_rel)
def test_no_filing_surface_offers_a_discretion_exemption(path: Path) -> None:
    """D-093 / D-092 / GI-001: the axis survives as discretion, not as a word.

    A file can lose every banned spelling and still tell its stream which
    findings to hold back, which is the same instruction with the vocabulary
    filed off. This sweep is over the whole filing corpus rather than the
    stream register, because the surfaces that write in their own voice --
    prove, trace, temper, sight -- are exactly the ones no word-identical pin
    reaches.
    """
    flat = _flat(path).lower()
    found = sorted(
        f"{phrase!r} ({why})" for phrase, why in _DISCRETION_EXEMPTIONS
        if phrase in flat
    )
    assert not found, (
        f"{_rel(path)} hands its stream discretion over a finding: {found}. "
        f"Every defect gets fixed, so there is nothing for a rank or a "
        f"deferral to decide. Scope a stream by its SUBJECT if you must -- "
        f"trace audits wiring, sight audits the rendered surface -- and close "
        f"the rule the way the sibling surfaces close theirs, with a sentence "
        f"that gives no discretion at all."
    )


def test_prove_assay_step_states_no_fix_order() -> None:
    """D-092: deleting the ranking is half; saying what replaced it is the rest.

    "HOLLOW verdicts are highest priority" was a fix-order instruction, and a
    step that simply stops giving one reads as silence to the next author who
    wants to split a queue in two -- the same reasoning
    ``test_sight_routes_its_findings_by_tier_not_by_a_work_effort_grade``
    records for sight's backlog.
    """
    flat = _flat(PROVE_SKILL)
    assert (
        "Nothing here ranks one verdict above another: every non-VERIFIED "
        "verdict is a defect and every defect gets fixed, so there is no fix "
        "order left for this step to state." in flat
    ), (
        "prove/SKILL.md's ASSAY step no longer states that it orders nothing. "
        "The sentence it replaced ranked HOLLOW above the other verdicts, one "
        "screen from the paragraph banning `priority` by name."
    )
    assert 'No exceptions, no deferrals, no "this one is only cosmetic."' in flat, (
        "prove/SKILL.md's ASSAY step lost the no-discretion close every other "
        "filing surface carries. A rule in this register closes on one."
    )


def test_prove_assess_step_describes_the_journey_without_triaging_it() -> None:
    """D-096: the file's own FAIL rule already ruled on this question.

    Step 2 asked which non-VERIFIED items sit on the core user journey, in a
    file whose verdict rules say there is "no off-the-critical-path exemption,
    because that was the severity axis wearing a different name". Asking the
    question is how the exemption gets computed; the answer is then one step
    from being acted on.
    """
    flat = _flat(PROVE_SKILL)
    assert (
        "an item nothing on the happy path reaches is a defect on exactly the "
        "same terms as one the first click hits" in flat
    ), (
        "prove/SKILL.md's Assess step no longer rules that placement on a "
        "journey changes nothing about a finding. Dropping the triage "
        "question without the ruling leaves the next author free to re-add it."
    )
    assert "Description, never triage" in flat, (
        "prove/SKILL.md's Assess step does not say which of the two the tally "
        "is. A journey tally that does not rule itself out of triage is a "
        "triage tally with a different heading."
    )


def test_prove_report_has_no_confidence_ladder() -> None:
    """D-096: a HIGH/MEDIUM/LOW rung over a whole report is the axis again.

    `tier` grades the evidence behind ONE finding. A single rung averaged over
    every finding in the report grades nothing anything downstream can act on,
    and reintroduces an ordered scale beside the closed two-member one.
    """
    flat = _flat(PROVE_SKILL)
    assert "quality confidence" not in flat, (
        "prove/SKILL.md's Overall Assessment demands a confidence grade again. "
        "GI-001 abolished the graded axis; `tier` replaced it per finding, not "
        "per report."
    )
    assert (
        "No confidence ladder over the report as a whole: `tier` already "
        "records the evidence behind each finding one finding at a time" in flat
    ), (
        "prove/SKILL.md's Overall Assessment no longer says why it asks for no "
        "confidence rung. Silence invites the ladder back the next time "
        "somebody wants a one-line summary."
    )


def test_trace_scopes_its_findings_by_subject_not_by_size() -> None:
    """D-093: 'only structural completeness gaps' was a withholding rule.

    A stream may be scoped by SUBJECT -- trace audits wiring and data flow,
    sight audits the rendered surface -- and that is a division of labour. It
    may not be scoped by how much a finding matters, which is what "Do NOT
    flag cosmetic/style issues" instructed in the release that abolished the
    axis deciding it.
    """
    flat = _flat(TRACE_SKILL)
    assert "**Scope is the subject, never the size**" in flat, (
        "trace/SKILL.md's constraints no longer distinguish scoping a stream "
        "by subject from grading its findings by size. Deleting the cosmetic "
        "exemption without that distinction loses the legitimate half: TRACE "
        "genuinely does not own the rendered surface."
    )
    assert (
        "Everything inside TRACE's own subject is a defect however small the "
        "fix looks, and this rule gives you no discretion to call one "
        '"cosmetic."' in flat
    ), (
        "trace/SKILL.md's scope rule lost its no-discretion close. "
        "agents/research-auditor.md closes the same ruling the same way, and "
        "without it a subject boundary reads as permission to judge size."
    )


@pytest.mark.parametrize("path", SCHEMA_BEARING_SKILLS, ids=lambda p: p.parent.name)
def test_skill_evidence_axis_constraint_closes_on_no_discretion(path: Path) -> None:
    """D-093: eight surfaces closed the rule; these two trailed off.

    Every stream agent's tier rule ends "No exceptions, no deferrals, no 'this
    one is only cosmetic.'" The two skills stated the same obligations and
    then stopped, which is the register's way of leaving a rule negotiable.
    """
    assert (
        "`tier` records evidence, never how much work a fix is worth. No "
        'exceptions, no deferrals, no "this one is only cosmetic."'
        in _flat(path)
    ), (
        f"{_rel(path)}'s evidence-axis constraint no longer closes on the "
        f"no-exceptions sentence its sibling filing surfaces use. A constraint "
        f"that states an obligation without closing it is one a stream reads "
        f"as advice."
    )


# ---------------------------------------------------------------------------
# GRIND cycle 5 -- the shared filing block, and the surface that took four of five
# ---------------------------------------------------------------------------

#: The five rules of agents/assayer.md's filing register, in the order their
#: back-references need. Two of them refer upward: the no-severity bullet names
#: "the `tier` axis the next rule makes required", and the `target_kind`
#: bullet's "That refusal is not automatic" and "the split above did nothing"
#: are both the comment-prose bullet. The block is ordered, not a set.
#:
#: A surface takes this register one of two ways, and the two are pinned
#: separately below because conflating them fails honest prose. sight and
#: temper take the block WHOLESALE -- they have no stream-specific voice for
#: it, so their copies are byte-identical to the source. The four stream agents
#: adapt three of the five to their own subject (tracer's class bullet counts
#: UNWIRED symbols behind an unregistered router; its comment-prose bullet ends
#: on wiring verdicts rather than the severity tier) and share only the two
#: tier rules verbatim, which is what
#: ``test_stream_agents_share_one_tier_rule_verbatim`` already pins.
_SHARED_FILING_BULLET_HEADS = (
    "- **Name the class when instances share a root cause.**",
    "- **No severity classification.**",
    "- **Set `tier` on every filing;",
    "- **Comment-prose findings are observations, not defects.**",
    "- **Declare `target_kind` on every filing.**",
)

_COMMENT_PROSE_HEAD = _SHARED_FILING_BULLET_HEADS[3]
_TARGET_KIND_HEAD = _SHARED_FILING_BULLET_HEADS[4]

#: The phrases in the `target_kind` bullet that point UP at the comment-prose
#: bullet. Located in the referring text rather than in a roster: what makes
#: the antecedent required is that this file's own sentence reaches for it, so
#: a surface writing a self-contained `target_kind` rule owes nothing --
#: agents/coverage-diff.md is exactly that case and is correct without the
#: comment-prose bullet.
_TARGET_KIND_BACK_REFERENCES = ("That refusal", "the split above")


def _shared_bullet(path: Path, head: str) -> str | None:
    """The one line in `path` opening with `head`, or None if it carries none."""
    hits = [line for line in _read(path).splitlines() if line.startswith(head)]
    assert len(hits) < 2, (
        f"{_rel(path)} carries {len(hits)} bullets opening {head!r}. Two copies "
        f"of one shared rule drift apart a clause at a time, and the doors "
        f"report ONE refusal per violation -- so the stream is taught two "
        f"vocabularies and the refusal matches at most one of them."
    )
    return hits[0] if hits else None


#: Surfaces that take the register WHOLESALE, derived by the bullet that marks
#: the wholesale copy: a file whose class bullet is byte-identical to the
#: source did not adapt the register to its own voice, so every other bullet in
#: the block is a copy too and is checked as one. Deriving is the point --
#: sight was reachable by every corpus in this module and still lost a bullet,
#: because nothing asked whether a surface that copies the block copies ALL of
#: it.
VERBATIM_FILING_BLOCK_SURFACES = tuple(
    p
    for p in DEFECT_FILING_SURFACES
    if _shared_bullet(p, _SHARED_FILING_BULLET_HEADS[0])
    == _shared_bullet(ASSAYER, _SHARED_FILING_BULLET_HEADS[0])
)


def test_the_verbatim_filing_block_roster_is_derived() -> None:
    """Floor check: a roster that lost sight would pass every pin below."""
    rel = {_rel(p) for p in VERBATIM_FILING_BLOCK_SURFACES}
    for expected in (ASSAYER, SIGHT_SKILL, TEMPER_SKILL):
        assert _rel(expected) in rel, (
            f"{_rel(expected)} no longer copies the class bullet verbatim, so "
            f"nothing below checks it carries the register whole. A surface "
            f"drops out of this derivation by rewording that bullet -- which "
            f"is legitimate for a stream with its own subject, and is why the "
            f"four stream agents are not swept here. If this file grew a voice "
            f"of its own, say so; if the bullet merely drifted, re-copy it."
        )
    assert TRACER not in VERBATIM_FILING_BLOCK_SURFACES, (
        "agents/tracer.md derived into the verbatim roster. It adapts three of "
        "the five bullets to wiring -- its class bullet counts UNWIRED symbols "
        "behind an unregistered router -- so demanding a byte-identical paste "
        "would pin away prose written for its own stream on purpose."
    )


@pytest.mark.parametrize("path", VERBATIM_FILING_BLOCK_SURFACES, ids=_rel)
@pytest.mark.parametrize("head", _SHARED_FILING_BULLET_HEADS, ids=lambda h: h[5:28])
def test_a_surface_copying_the_filing_block_copies_all_of_it(
    path: Path, head: str
) -> None:
    """D-092's neighbour: the block is five rules, and four is not most of it.

    skills/sight/SKILL.md carried four -- it dropped the comment-prose bullet
    and kept the `target_kind` bullet that points at it. Each missing bullet is
    a rule the stream does not have while filing into the same ledger through
    the same doors, and the doors do not soften for a surface that failed to
    mention one.
    """
    source = _shared_bullet(ASSAYER, head)
    assert source is not None, (
        f"agents/assayer.md no longer carries {head!r}, so there is no source "
        f"to copy. It is the register the other surfaces mirror; fix it there "
        f"rather than dropping this check."
    )
    assert _shared_bullet(path, head) == source, {
        "file": _rel(path),
        "bullet": head,
        "carries_it_at_all": _shared_bullet(path, head) is not None,
        "why": (
            "this surface copies the filing register wholesale and either "
            "omits this bullet or words it differently. Copy the line from "
            "agents/assayer.md byte for byte; if the rule itself is wrong, "
            "change it at the source and re-copy everywhere it landed."
        ),
    }


@pytest.mark.parametrize("path", VERBATIM_FILING_BLOCK_SURFACES, ids=_rel)
def test_the_verbatim_filing_block_keeps_its_order(path: Path) -> None:
    """Presence is not enough when two bullets refer upward."""
    text = _read(path)
    bullets = [_shared_bullet(path, head) for head in _SHARED_FILING_BULLET_HEADS]
    assert all(b is not None for b in bullets), (
        f"{_rel(path)} is missing a shared filing bullet; the per-bullet "
        f"assertion above names which one."
    )
    positions = [text.index(b) for b in bullets]
    assert positions == sorted(positions), (
        f"{_rel(path)}'s filing bullets are out of order. Expected "
        f"{list(_SHARED_FILING_BULLET_HEADS)}: the no-severity bullet names "
        f"'the `tier` axis the next rule makes required', so the tier bullet "
        f"follows it, and the `target_kind` bullet's 'That refusal' and 'the "
        f"split above' are the comment-prose bullet, so it precedes."
    )


@pytest.mark.parametrize("path", DEFECT_FILING_SURFACES, ids=_rel)
def test_a_back_referencing_target_kind_rule_has_its_antecedent(path: Path) -> None:
    """The defect sight actually shipped: a pointer with nothing under it.

    sight's `target_kind` bullet opened "That refusal is not automatic" and
    closed "the split above did nothing" while the split it names -- the
    comment-prose bullet -- was absent from the file. A reader who cannot find
    an antecedent supplies one, and the nearest candidate two bullets up is the
    no-severity rule, which turns "that refusal" into the severity ban and
    reads `target_kind` as something the server already handles.

    Derived from the REFERRING TEXT, not from a roster: a surface that writes a
    self-contained `target_kind` rule owes no antecedent, which is why
    agents/coverage-diff.md passes without a comment-prose bullet.
    """
    rule = _shared_bullet(path, _TARGET_KIND_HEAD)
    if rule is None:
        pytest.skip(f"{_rel(path)} states no `target_kind` bullet")
    refs = [phrase for phrase in _TARGET_KIND_BACK_REFERENCES if phrase in rule]
    if not refs:
        pytest.skip(f"{_rel(path)}'s `target_kind` rule refers to nothing above it")
    antecedent = _shared_bullet(path, _COMMENT_PROSE_HEAD)
    assert antecedent is not None, (
        f"{_rel(path)}'s `target_kind` rule says {refs} and the file carries no "
        f"comment-prose bullet for those to mean. Either paste "
        f"agents/assayer.md's {_COMMENT_PROSE_HEAD!r} bullet above it, or "
        f"rewrite the `target_kind` rule to stand on its own the way "
        f"agents/coverage-diff.md's does. A dangling back-reference is worse "
        f"than a missing rule: the reader resolves it against whatever is "
        f"nearest."
    )
    text = _read(path)
    assert text.index(antecedent) < text.index(rule), (
        f"{_rel(path)}'s comment-prose bullet sits BELOW the `target_kind` rule "
        f"that says {refs}. 'The split above' is a direction, and a reader who "
        f"looks up and finds the wrong rule does not keep looking."
    )


# ---------------------------------------------------------------------------
# D-099 -- the shape a surface DOCUMENTS must survive the door it names
# ---------------------------------------------------------------------------
#
# Every pin above this one asks whether a filing surface SAYS the right thing.
# None of them asks whether the JSON it hands a stream to copy is a filing the
# doors would accept. That is a different question and it has a different
# failure mode: the reader who copies the shape never reads the prose beside
# it, so a shape and a rung can disagree indefinitely while every substring
# assertion in this module stays green.
#
# Driven, at the moment D-099 was filed: five of the six documented
# `"tier": "LATENT"` examples -- agents/assayer.md, agents/tracer.md,
# agents/flow-tracer.md, agents/coverage-diff.md and skills/sight/SKILL.md --
# were REFUSED by ``validate_defect_filing`` naming field `file_path`, because
# none carried a `file` key. Only agents/research-auditor.md's was accepted. A
# stream copying the shape its own instructions ship met a refusal naming a
# field those instructions never mentioned, and had nothing to read to recover.
# That is D-017's shape exactly: the shape a stream copies is what the door
# then refuses.
#
# The assertion is a DERIVATION over the real validator rather than a list of
# required keys re-typed here. A key list would have to be re-decided every
# time a rung moves -- and a rung moving is precisely the event that breaks the
# examples, so re-typing it would put the bug and its check on the same side of
# the change.


#: The derived tier this sweep selects on. ``vocab.py`` exports the closed SET
#: and no per-member name, so the member is spelled here once and immediately
#: checked back against the set -- a rename in vocab fails on the next line
#: rather than silently emptying every sweep below it.
_LATENT = "LATENT"
assert _LATENT in vocab.DEFECT_TIERS, (
    f"{_LATENT!r} is no longer a member of vocab.DEFECT_TIERS "
    f"({sorted(vocab.DEFECT_TIERS)}). Every LATENT sweep in this module "
    f"selects on it and would go silently empty; re-spell it here and in the "
    f"prose the pins below read."
)


def _documented_latent_examples(path: Path) -> list[dict]:
    """Every `tier: LATENT` record a surface's normative JSON examples ship."""
    try:
        records = _example_records(path)
    except (json.JSONDecodeError, AssertionError):
        return []
    return [r for r in records if r.get("tier") == _LATENT]


def test_the_latent_example_sweep_is_not_vacuous() -> None:
    """Floor check: a sweep that finds no examples asserts nothing.

    The parametrised check below passes trivially on a surface that documents
    no LATENT example at all, which is correct -- prove, trace, temper and
    teammate state the tier obligations without shipping a `defects` array. It
    also means the whole sweep could go silently empty if every stream agent
    lost its LATENT example in one edit. This is the floor that fails first.
    """
    carriers = {
        _rel(p) for p in DEFECT_FILING_SURFACES if _documented_latent_examples(p)
    }
    missing = sorted({_rel(p) for p in STREAM_AGENTS} - carriers)
    assert not missing, (
        f"{missing} document no `tier`: {_LATENT!r} example any "
        f"more. D3 requires at least one entry in each stream agent's report "
        f"shape showing the LATENT tier beside its `reproduction_attempted` "
        f"string -- without it a stream has read the rule and never seen the "
        f"shape, and the sweep below has nothing to drive."
    )


@pytest.mark.parametrize("path", DEFECT_FILING_SURFACES, ids=_rel)
def test_every_documented_latent_example_survives_the_filing_door(path: Path) -> None:
    """D-099 / CT-001 / CT-002: the documented shape is an ACCEPTED filing.

    ``validate_defect_filing`` is the one place both doors decide, so driving
    the examples through it drives them through ``Foundry-Defect`` and
    ``Foundry-Sync`` at once -- which is the point, because a shape accepted at
    one door and refused at the other is worse than a shape refused at both.
    """
    for record in _documented_latent_examples(path):
        refusal = foundry_doors.validate_defect_filing(record)
        assert refusal is None, {
            "file": _rel(path),
            "why": (
                "this surface documents a LATENT filing shape that the shared "
                "filing validator REFUSES. A stream that copies its own "
                "instructions' example meets a refusal naming a field those "
                "instructions never taught it, and the refusal text is the "
                "first it hears of the rung. Fix the EXAMPLE (or the rung), "
                "never this assertion."
            ),
            "refused_field": refusal.get("field") if refusal else None,
            "refusal": refusal,
            "example_keys": sorted(record),
        }


#: FR-004's location clause, word-identical across the four stream agents. Not
#: folded into ``_TIER_RULE_CLAUSES`` and not placed inside the shared tier
#: span, because that span is shared with agents/coverage-diff.md and
#: skills/sight/SKILL.md, whose own report shapes this rule does not describe.
_LATENT_LOCATION_RULE = (
    "- **Name a location on every `LATENT` filing.** A `LATENT` record is carried "
    "into the report's LATENT backlog as a promise that a later cycle can go and "
    "drive it, and a row carrying a description and no path is a promise nothing "
    "can collect."
)


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_each_stream_agent_asks_for_a_location_on_a_latent_filing(path: Path) -> None:
    """D-099: expected, and said to be expected rather than refused.

    The rung that DEMANDED a location was removed deliberately -- a LATENT
    filing that genuinely cannot be located is better recorded unlocated than
    re-worded until it claims a path the stream never had. That makes the
    location a matter of prose, and prose is exactly what the report's backlog
    quality now rests on. Asserted as one exact sentence across all four files
    so a three-of-four edit fails, in the discipline
    ``test_stream_agents_share_one_tier_rule_verbatim`` holds for the rule above
    it.
    """
    assert _LATENT_LOCATION_RULE in _flat(path), (
        f"{_rel(path)} no longer tells its stream to name a location on a "
        f"LATENT filing. The doors do not refuse one without it, so nothing "
        f"else will catch the omission -- it surfaces as a backlog row a later "
        f"cycle cannot open, one cycle after the stream that could have "
        f"located it has finished."
    )


@pytest.mark.parametrize("path", STREAM_AGENTS, ids=lambda p: p.name)
def test_the_location_rule_says_expected_rather_than_required(path: Path) -> None:
    """D-099's reversal, pinned so it cannot be re-tightened by accident.

    An earlier ruling made `file_path` a rung on the LATENT lane and this rule
    is what replaced it. Written as a hard requirement the prose would promise
    a refusal that does not happen, which is the FALSE_DOCUMENTED_CONTRACT
    class in the other direction -- a stream that reads "required" and cannot
    locate its finding either files a fabricated path or files nothing.
    """
    flat = _flat(path)
    assert "This one is EXPECTED rather than refused" in flat, (
        f"{_rel(path)}'s LATENT-location rule no longer says the doors ACCEPT "
        f"a filing without one. A rule that reads as a rung promises a refusal "
        f"the server does not issue."
    )
    assert "renders that row as unlocated" in flat, (
        f"{_rel(path)} no longer says what the report does with an unlocated "
        f"LATENT row. Unstated, 'expected but not refused' reads as 'ignored', "
        f"and the reason to supply the path disappears with it."
    )


# ---------------------------------------------------------------------------
# D-104 / AC-018 / GI-008 -- the recorded PROVE width reaches the stream
# ---------------------------------------------------------------------------
#
# The DELTA roster was computed, recorded, returned and DISPLAYED, and never
# reached the one agent it scopes. ``_prove_delta_sample`` drew the rows,
# ``inspect_start`` persisted them as ``prove_sample``, ``foundry_next_action``
# returned them and display.py rendered "PROVE: N row(s)". The consumer side
# was silent: agents/assayer.md -- which commands/start.md names as the PROVE
# agent -- mentioned neither the roster, nor the DELTA width, nor Foundry-Next
# as somewhere a width is read from, so a DELTA INSPECT still cost a
# full-width PROVE and AC-018's saving was unrealised on the guided path.
#
# start.md states the principle this pin enforces, about the observation split,
# the tier rule and the progress ledger alike: each reaches the streams
# "through their own files rather than through anything you paste, so it is in
# force on a fresh checkout". The roster was the one stream-facing rule written
# into no agent file, reachable only if the lead remembered to paste it.
#
# Both surfaces are pinned because both are load-bearing and neither implies
# the other: the agent file is what the F2 spawn loads, and the skill is what
# `/foundry:prove` runs.

_PROVE_WIDTH_SURFACES = (ASSAYER, PROVE_SKILL)

#: One claim per entry, with the failure its absence causes. Word-identical
#: across both surfaces on purpose -- the two documents have different voices,
#: but a width is a mechanism, and two paraphrases of a field path are two
#: chances to name a key the server does not return.
_PROVE_WIDTH_CLAUSES = (
    (
        "Call `Foundry-Next` and read `inspect_mode` out of the RESPONSE",
        "the surface no longer says WHERE the width is read from. GI-008 puts "
        "the decision at the transition that opens the INSPECT and leaves "
        "Foundry-Next only reporting it; a stream told neither reads no width "
        "at all and runs the matrix.",
    ),
    (
        "On `DELTA`, verify exactly the rows in `inspect_mode.prove_sample`",
        "the surface no longer names the roster field or says the roster is "
        "the whole job on a DELTA cycle. `_coverage_shortfall`'s DELTA arm "
        "measures `checked >= len(roster)` against exactly this list.",
    ),
    (
        "On `FULL`, verify the whole matrix",
        "the surface no longer says what FULL means, so the narrowing reads as "
        "unconditional and a final-gate INSPECT silently runs at DELTA width.",
    ),
    (
        "`Foundry-Stream` with `stream: \"prove\"`, `cycle`, `items_checked`, "
        "`items_total` and `findings_count`",
        "the surface no longer tells PROVE how to report its coverage. A "
        "stream that cannot mark itself complete contributes no coverage to "
        "the cycle's roll-up, where its absence reads as no coverage rather "
        "than as a broken call -- and `Foundry-Phase('inspect_clean')` then "
        "refuses the cycle naming a stream that did all of its work.",
    ),
    (
        "never the terminal line",
        "the surface no longer distinguishes the roster from the display of "
        "it. display.py truncates the printed sample, so a stream reading the "
        "terminal line reads a prefix and reports a width it never ran.",
    ),
    (
        "eight rows",
        "the surface no longer states WHERE the display truncates. 'It is "
        "truncated' with no number leaves a reader unable to tell a short "
        "roster from a clipped one.",
    ),
    (
        "no narrowing was decided",
        "the surface no longer says what an ABSENT width means. Unstated, a "
        "stream that finds no `inspect_mode` picks a width itself, which is "
        "the lazily-computed mode GI-008 and GI-009 both name as the "
        "violation.",
    ),
)


@pytest.mark.parametrize("path", _PROVE_WIDTH_SURFACES, ids=_rel)
@pytest.mark.parametrize("clause,why", _PROVE_WIDTH_CLAUSES, ids=lambda v: v[:44])
def test_prove_reads_the_width_the_server_recorded(
    path: Path, clause: str, why: str
) -> None:
    """AC-018 / D-104, one claim at a time, on both PROVE surfaces."""
    assert clause in _flat(path), f"{_rel(path)}: {why}"


def test_the_prove_roster_key_the_prose_names_is_the_one_the_gate_reads(
    tmp_path: Path,
) -> None:
    """D-104's floor: a field path is only prose until something reads it.

    Both surfaces tell PROVE to read `inspect_mode.prove_sample`. If the
    recorded decision ever spelled that key differently, the instruction would
    send every DELTA stream to a key that is never there -- and the stream's
    honest response to a missing roster is "run the whole matrix", which is
    indistinguishable from the pre-D-104 behaviour the pins above would still
    call green.

    So the key is driven through ``_recorded_prove_roster`` -- the function
    ``_coverage_shortfall`` consults to decide whether a DELTA PROVE covered
    its width -- rather than being asserted against a constant re-typed here.
    """
    key = "prove_sample"
    for surface in _PROVE_WIDTH_SURFACES:
        assert f"`inspect_mode.{key}`" in _flat(surface), (
            f"{_rel(surface)} names a roster field other than {key!r}; this "
            f"derivation and the prose have come apart."
        )

    fdir = tmp_path / foundry_state.ARCHIVE_DIR / "d104-roster"
    fdir.mkdir(parents=True, exist_ok=True)
    roster = ["FR-007", "US-002"]
    # The top-level `cycle` matches the entry's stamp because a run whose
    # counter says 0 while its INSPECT was opened for cycle 6 is an archive no
    # transition writes (D-216: every crossing stamps the counter it holds).
    # This read passes its cycle explicitly, so the fixture would resolve
    # either way — but a fixture is a claim about a real archive, and the one
    # below is the shape `inspect_start` leaves behind.
    (fdir / "state.json").write_text(
        json.dumps(
            {
                "cycle": 6,
                "inspect_modes": [
                    {"mode": "DELTA", "cycle": 6, "rule": "delta", key: roster}
                ],
            }
        ),
        encoding="utf-8",
    )
    assert streams._recorded_prove_roster(fdir, 6) == roster, (
        f"the recorded decision's {key!r} list is not what the streams-complete "
        f"check reads back. Both PROVE surfaces tell the stream to check "
        f"exactly that list, so a rename here makes the instruction point at "
        f"nothing -- silently, because a stream that finds no roster correctly "
        f"falls back to the whole matrix."
    )


# ---------------------------------------------------------------------------
# D-160 / AC-019 / FR-012 / US-004 -- the recorded TRACE width reaches BOTH
# of its surfaces, not just the agent one
# ---------------------------------------------------------------------------
#
# D-140 closed the TRACE half of the hole D-104 closed for PROVE -- on one
# surface. It emitted ``touched_files`` and ``diff_base`` from the recorded
# decision and taught agents/tracer.md to read them, and left
# skills/trace/SKILL.md -- the surface ``/foundry:trace`` runs -- scoping the
# walk from the spec alone, which is the exact pre-D-140 state its own test
# docstring names. Driven at the filing commit over the shipped artifacts:
# ``grep -ciE 'inspect_mode|touched_file|diff_base'`` returned 0 for the skill
# against 8 for the agent, 7 for skills/prove/SKILL.md and 8 for
# agents/assayer.md, and the skill's Step 1d still said "Cross-reference
# inventory against the spec" with nothing narrowing it. So on a DELTA cycle
# whose recorded decision named ``stream_scope.trace.scope == "delta"`` and
# ``touched_files == ["src/handler.py"]``, ``/foundry:trace`` walked the whole
# spec and reported a coverage pair against a denominator the gate never drew.
#
# The principle was ALREADY written down, thirty lines above this comment:
# "Both surfaces are pinned because both are load-bearing and neither implies
# the other: the agent file is what the F2 spawn loads, and the skill is what
# `/foundry:prove` runs." It was applied to PROVE and not to TRACE. That is
# what makes this a class rather than a one-file omission, and it is why this
# pin is parametrised over the SURFACE PAIR from the start: a third TRACE
# surface joins ``_TRACE_WIDTH_SURFACES`` rather than getting a test of its
# own, which is the shape that would have caught D-160 at D-140 time.

_TRACE_WIDTH_SURFACES = (TRACER, TRACE_SKILL)

#: One claim per entry, with the failure its absence causes. Word-identical
#: across both surfaces for the same reason the PROVE clauses are: the two
#: documents have different voices and different step numbering, but a field
#: path is a mechanism, and two paraphrases of one are two chances to name a
#: key the server does not return.
_TRACE_WIDTH_CLAUSES = (
    (
        "Call `Foundry-Next` and read `inspect_mode` out of the RESPONSE",
        "the surface no longer says WHERE the width is read from. GI-008 puts "
        "the decision at the transition that opens the INSPECT and leaves "
        "Foundry-Next only reporting it; a stream told neither reads no width "
        "at all and walks the whole spec.",
    ),
    (
        "`inspect_mode.stream_scope.trace.scope`",
        "the surface no longer names the PER-STREAM scope key. `mode` alone is "
        "not this stream's width: `_decide_inspect_mode` records a DELTA cycle "
        "on which TRACE's own scope is `full`, and a surface reading only the "
        "mode narrows a walk the server widened.",
    ),
    (
        "walk exactly the symbols declared in `inspect_mode.touched_files`",
        "the surface no longer names the TRACE roster field or says the roster "
        "is the whole walk on a DELTA cycle. `_maybe_skip_trace` reads exactly "
        "this list to decide whether TRACE has symbols to walk at all.",
    ),
    (
        "`inspect_mode.diff_base`",
        "the surface no longer says what the touched-file list was measured "
        "FROM. Unstated, a stream cannot tell the roster the boundary drew "
        "from a diff it could compute itself, and computing it itself is the "
        "lazily-derived width GI-008 names as the violation.",
    ),
    (
        "On `FULL`, walk every declared symbol",
        "the surface no longer says what FULL means, so the narrowing reads as "
        "unconditional and a final-gate INSPECT silently walks at DELTA width.",
    ),
    (
        "never the terminal line",
        "the surface no longer distinguishes the roster from the display of "
        "it. display.py truncates the printed file list, so a stream reading "
        "the terminal line reads a prefix and reports a width it never ran.",
    ),
    (
        "five files",
        "the surface no longer states WHERE the display truncates. 'It is "
        "truncated' with no number leaves a reader unable to tell a short "
        "roster from a clipped one.",
    ),
    (
        "no narrowing was decided",
        "the surface no longer says what an ABSENT width means. Unstated, a "
        "stream that finds no `inspect_mode` picks a width itself, which is "
        "the lazily-computed mode GI-008 and GI-009 both name as the "
        "violation.",
    ),
    (
        "walk everything",
        "the surface no longer names the fallback ACTION for an absent or "
        "wrong-cycle width. Saying the width is missing without saying what to "
        "do about it leaves the narrowing as the only instruction on the page.",
    ),
)


@pytest.mark.parametrize("path", _TRACE_WIDTH_SURFACES, ids=_rel)
@pytest.mark.parametrize("clause,why", _TRACE_WIDTH_CLAUSES, ids=lambda v: v[:44])
def test_trace_reads_the_width_the_server_recorded(
    path: Path, clause: str, why: str
) -> None:
    """AC-019 / D-160, one claim at a time, on both TRACE surfaces."""
    assert clause in _flat(path), f"{_rel(path)}: {why}"


def test_the_trace_skill_scopes_its_coverage_pair_to_the_recorded_width() -> None:
    """D-160's second surface: the skill also REPORTS against the width.

    Naming the roster in a width step and then telling the stream to count
    `items_checked` against the spec two screens later is the same defect with
    an extra step: `_coverage_shortfall` compares the reported pair against the
    width the server drew, so a pair measured against the manifest reads as a
    coverage drop on a cycle that walked exactly what it was asked to.

    agents/tracer.md states this inside its width step; skills/trace/SKILL.md
    states it where the skill actually calls `Foundry-Stream`, which is a
    different place in the document and therefore its own assertion.
    """
    flat = _flat(TRACE_SKILL)
    assert (
        "Take `items_checked` and `items_total` from the width Step 0.5 read"
        in flat
    ), (
        "skills/trace/SKILL.md's Foundry-Stream step no longer ties the "
        "coverage pair to the recorded width. skills/prove/SKILL.md ties its "
        "own pair the same way, in the same words."
    )
    assert (
        "they are counted against `inspect_mode.touched_files`, not against "
        "the spec" in flat
    ), (
        "skills/trace/SKILL.md no longer names the roster the DELTA pair is "
        "counted against. 'Use the width' without the field name sends the "
        "stream back to the spec, which is the denominator D-160 filed."
    )
    assert "the width Step 0.5 read" in flat, (
        "skills/trace/SKILL.md's coverage instruction no longer points back at "
        "a width step by name; a reader who joined at Step 5 has no way to "
        "learn one exists."
    )


def test_the_trace_roster_key_the_prose_names_is_the_one_the_server_reads(
    tmp_path: Path,
) -> None:
    """D-160's floor, in the shape D-104's floor takes for PROVE.

    Both TRACE surfaces now tell the stream to walk `inspect_mode.touched_files`.
    If the recorded decision ever spelled that key differently, the instruction
    would send every DELTA stream to a key that is never there -- and a stream
    that finds no roster correctly falls back to walking everything, which is
    indistinguishable from the pre-D-160 behaviour the substring pins above
    would still call green.

    So the key is driven through ``_maybe_skip_trace`` -- the function that
    decides, from the recorded decision alone, whether a DELTA cycle leaves
    TRACE any symbols to walk -- rather than being asserted against a constant
    re-typed here.
    """
    key = "touched_files"
    for surface in _TRACE_WIDTH_SURFACES:
        assert f"`inspect_mode.{key}`" in _flat(surface), (
            f"{_rel(surface)} names a roster field other than {key!r}; this "
            f"derivation and the prose have come apart."
        )

    fdir = tmp_path / foundry_state.ARCHIVE_DIR / "d160-roster"
    fdir.mkdir(parents=True, exist_ok=True)
    roster = ["src/handler.py"]
    # The top-level `cycle` is load-bearing here, and its absence is what made
    # this fixture stop describing a real run. `_maybe_skip_trace` asks
    # `_current_inspect_mode` about the cycle the COUNTER holds, and since
    # D-216 that read answers None for an entry stamped for any other crossing
    # — so a state.json carrying a cycle-9 decision beside a counter reading 0
    # (the default for an absent key) is an unrecorded width, and this
    # function returns the `_unrecorded_width_problem` refusal, which carries
    # a `reason` and a `hint` and no `details` at all. The assertion below then
    # died on the missing key rather than on the roster it is here to drive.
    # The repair is the archive, not the read: every transition that opens an
    # INSPECT stamps the counter it holds inside the same transaction, so a
    # decision for cycle 9 only ever sits beside a counter at 9.
    (fdir / "state.json").write_text(
        json.dumps(
            {
                "phase": "F2",
                "cycle": 9,
                "inspect_modes": [
                    {
                        "mode": "DELTA",
                        "cycle": 9,
                        "rule": "delta",
                        "stream_scope": {"trace": {"scope": "delta"}},
                        key: roster,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    decision = width._maybe_skip_trace(fdir, str(tmp_path))

    assert decision is not None and decision["skip"] is False, (
        f"the recorded decision's {key!r} list is not what the server reads "
        f"back when it decides whether TRACE has symbols to walk. Both TRACE "
        f"surfaces tell the stream to walk exactly that list, so a rename here "
        f"makes the instruction point at nothing -- silently, because a stream "
        f"that finds no roster correctly falls back to walking everything."
    )
    assert decision["details"][key] == roster, decision


# ---------------------------------------------------------------------------
# D-174 / FR-012 / FR-047 / AC-019 / US-004 -- the roster the four surfaces
# send a stream to read is a roster the MCP boundary actually carries
# ---------------------------------------------------------------------------
#
# D-104, D-140, D-160 and D-161 each wired one more field into `inspect_mode`
# and taught one more surface to read it "out of the RESPONSE". None of them
# checked that a response HAD a body. It did not: `server.py#call_tool`
# returned exactly `format_result(name, result)`, and `format_result` returns
# ONLY the formatter's rendering whenever a formatter exists -- which it does
# for `Foundry-Next`. So the dict was built, populated and discarded one rung
# below the boundary, and the four surfaces' instruction named a field that
# crossed nothing while FORBIDDING the truncated line that was the only thing
# a stream could see. A TRACE stream hit it first-hand on its opening call.
#
# D-173 fixed the boundary (`format_result_blocks`: the display, then
# `RESULT_JSON_MARKER`, then the whole result as JSON, with no fence because
# defect descriptions carry backtick runs). This section is the consumer half:
# the prose now says WHERE, and says it word-identically on all four surfaces,
# because the previous four fixes each taught one surface and the class was
# always that the other surfaces did not learn.
#
# The pins below are driven, not described. The marker the prose names is READ
# from `display.RESULT_JSON_MARKER` rather than re-typed here, so a respelling
# on either side fails naming the four files; and the roster is recovered from
# a real `format_result_blocks` rendering, so "the arrays cross" is a fact this
# module establishes rather than a claim it repeats.

from foundry_mcp.tools import display as _display  # noqa: E402

#: Every surface that tells a stream to read its width out of the response.
#: The union of the two width rosters rather than a third hand-written tuple:
#: a fifth width surface joins one of those and is swept here automatically,
#: which is the shape whose absence made D-104 -> D-140 -> D-160 -> D-174 four
#: separate defects instead of one.
_WIDTH_SURFACES = tuple(
    sorted(set(_TRACE_WIDTH_SURFACES) | set(_PROVE_WIDTH_SURFACES), key=_rel)
)

#: One claim per entry, word-identical across all four surfaces. The register
#: is _TRACE_WIDTH_CLAUSES': a field path is a mechanism, and two paraphrases
#: of one are two chances to send a stream at something that is not there.
_MARKER_CLAUSES = (
    (
        "A formatted tool's response is the rendered display, then a line "
        "reading",
        "the surface no longer says what a response is SHAPED like. 'Read it "
        "out of the RESPONSE' with no account of where in the response is the "
        "instruction D-174 filed -- true-sounding, and satisfiable only by "
        "the truncated line the same paragraph forbids.",
    ),
    (
        "then the complete result as JSON",
        "the surface no longer says the part after the marker is the WHOLE "
        "result. A stream that expects a projection looks for a key the "
        "boundary never invents and falls back to the display.",
    ),
    (
        "every array in full, nothing truncated",
        "the surface no longer says the arrays cross WHOLE. That is the one "
        "property that distinguishes the machine-readable half from the box "
        "above it, and without it a stream has no reason to prefer either.",
    ),
    (
        "no fence to strip and no terminator to find",
        "the surface no longer says how the JSON ENDS. D-173 chose a marker "
        "and no fence because result dicts carry backtick runs; a stream that "
        "hunts for a closing fence finds one inside a defect description and "
        "parses half a roster.",
    ),
    (
        "`json.loads` it and read `inspect_mode` off the object it returns",
        "the surface no longer names the operation. Naming the location "
        "without naming the parse leaves the stream reading the JSON as text, "
        "which is grepping a roster -- the failure mode one rung over.",
    ),
    (
        "a tool with no display formatter appends no marker",
        "the surface no longer states the exception. `format_result_blocks` "
        "adds the marker only for a tool in `_FORMATTERS`; a stream told the "
        "marker is universal treats its absence as a broken response instead "
        "of as a response that is already JSON.",
    ),
)


def test_the_width_surface_roster_is_derived() -> None:
    """Floor check: the sweep below is vacuous on an empty roster.

    ``_WIDTH_SURFACES`` is built from the two existing width rosters, so it
    empties if either is emptied -- and a parametrised test over an empty
    roster reports zero cases and passes the run. This fails first, and names
    the four files by the constants they must come from.
    """
    assert len(_WIDTH_SURFACES) == 4, (
        f"_WIDTH_SURFACES derived {[_rel(p) for p in _WIDTH_SURFACES]}, not "
        f"the four width surfaces. It is the union of _TRACE_WIDTH_SURFACES "
        f"and _PROVE_WIDTH_SURFACES; if a fifth surface was added, raise this "
        f"count deliberately rather than dropping the floor."
    )
    for expected in (TRACER, TRACE_SKILL, ASSAYER, PROVE_SKILL):
        assert expected in _WIDTH_SURFACES, (
            f"{_rel(expected)} is no longer a width surface. It is what the "
            f"F2 spawn loads or what the slash command runs, and a width rule "
            f"it does not carry is a rule that reaches its stream through "
            f"nothing."
        )


@pytest.mark.parametrize("path", _WIDTH_SURFACES, ids=_rel)
@pytest.mark.parametrize("clause,why", _MARKER_CLAUSES, ids=lambda v: v[:44])
def test_every_width_surface_says_where_in_the_response(
    path: Path, clause: str, why: str
) -> None:
    """D-174, one claim at a time, on all four surfaces at once."""
    assert clause in _flat(path), f"{_rel(path)}: {why}"


@pytest.mark.parametrize("path", _WIDTH_SURFACES, ids=_rel)
def test_the_marker_each_surface_names_is_the_one_the_boundary_emits(
    path: Path,
) -> None:
    """D-174's floor: a locator is only prose until something emits it.

    The marker is READ from ``display.RESULT_JSON_MARKER`` rather than typed
    here, in the discipline ``_EXPECTED_TYPE_ENUM`` holds for the type enum. A
    respelling on the display side then fails HERE, naming the prose files
    that have to follow it -- rather than shipping four surfaces that send
    every stream looking for a line the boundary stopped writing.
    """
    assert _display.RESULT_JSON_MARKER in _flat(path), (
        f"{_rel(path)} names a marker other than "
        f"{_display.RESULT_JSON_MARKER!r}. The prose and the boundary have "
        f"come apart: a stream splitting the response on the line this file "
        f"names finds nothing, and its honest fallback is the truncated "
        f"display -- which is the pre-D-173 behaviour every pin above would "
        f"still call green."
    )


def test_the_untruncated_roster_is_recoverable_the_way_the_prose_says() -> None:
    """D-174 end to end: the instruction, executed.

    The four surfaces now tell a stream to split the response on the marker,
    ``json.loads`` the remainder and read ``inspect_mode`` off it. That
    instruction is worth nothing unless following it LITERALLY yields the
    untruncated roster, so this test follows it literally -- no knowledge of
    ``format_result_blocks``'s internals beyond the public marker constant.

    Both halves are asserted, because D-173's rule is that neither replaces
    the other: the display stays truncated for the operator (NFR-005) and the
    JSON stays whole for the parser. A fix that widened the display instead
    would pass a naive check and cost the readability the truncation buys.
    """
    touched = [f"src/mod_{i}.py" for i in range(9)]
    sample = [f"FR-{i:03d}" for i in range(10)]
    result = {
        "action": "dispatch",
        "inspect_mode": {
            "mode": "DELTA",
            "rule": "delta",
            "cycle": 7,
            "diff_base": "abc1234",
            "touched_files": touched,
            "prove_sample": sample,
            "stream_scope": {"trace": {"scope": "delta", "detail": "9 files"}},
        },
    }

    rendered = _display.format_result_blocks("Foundry-Next", result)

    assert _display.RESULT_JSON_MARKER in rendered, (
        "Foundry-Next's response carries no marker line, so the instruction "
        "all four width surfaces give is unfollowable and every DELTA stream "
        "is back to reading the truncated display."
    )
    display_half, _, json_half = rendered.partition(_display.RESULT_JSON_MARKER)
    recovered = json.loads(json_half)["inspect_mode"]

    assert recovered["touched_files"] == touched, (
        f"following the prose recovers {len(recovered['touched_files'])} of "
        f"{len(touched)} touched files. The TRACE surfaces tell the stream "
        f"this list is its whole walk, so a truncation here is a walk the "
        f"stream reports having run and did not."
    )
    assert recovered["prove_sample"] == sample, (
        f"following the prose recovers {len(recovered['prove_sample'])} of "
        f"{len(sample)} roster rows. `_coverage_shortfall`'s DELTA arm "
        f"measures `checked >= len(roster)` against the full list, so a "
        f"truncation here refuses a PROVE stream that did all of its work."
    )
    assert recovered["stream_scope"]["trace"]["scope"] == "delta", (
        "the per-stream scope did not survive the boundary. `mode` alone is "
        "not a stream's width, which is the whole reason the TRACE surfaces "
        "name this key."
    )

    assert touched[5] not in display_half, (
        "the DISPLAY half stopped truncating. The four surfaces say the "
        "printed list is truncated at five files and that copying it is the "
        "error; if the box now shows everything, the prose is wrong in the "
        "other direction -- and NFR-005's readable terminal was the reason "
        "the summary existed."
    )
    assert sample[8] not in display_half, (
        "the DISPLAY half stopped truncating the PROVE roster at eight rows, "
        "which both PROVE surfaces state as the number a stream must not "
        "copy."
    )


# ---------------------------------------------------------------------------
# D-108 / FR-019 -- one hash spelling, driven rather than described
# ---------------------------------------------------------------------------
#
# agents/teammate.md tells every teammate to compute its own prompt hash and
# state it back, and both consuming gates refuse when the value differs from
# the file's. That contract holds only while the documented command and the
# gate compute the SAME digest -- and the two spellings that shipped agreed on
# every file containing no carriage return and diverged on the first one that
# did. Driven on a CRLF prompt: the dispatch block advertised one value, the
# documented command produced another, and Foundry-Accept-Casting refused with
# `stale_prompt_hash` whose remedy -- "re-read the prompt file in full and
# state its hash character for character" -- reproduces the same rejected value
# forever, because the reading was never the broken part. Casting prompts quote
# spec text verbatim, so one CR in a spec makes that casting's acceptance gate
# unpassable.
#
# A substring pin cannot see this: both spellings contain "sha256" and both are
# correct-looking. Only running the command the file publishes, on a file with
# CRLF endings, and handing the result to the gate, can tell them apart.

_HASH_FENCE_RE = re.compile(r"```bash\n(.*?)\n```", re.S)
_CRLF_PROMPT = b"# Casting 9 prompt\r\n\r\nRead this file in full.\r\n"


def _documented_hash_commands() -> list[str]:
    """Every fenced bash block in teammate.md's Step 0 hash paragraph."""
    text = _read(TEAMMATE)
    start = text.index("### Step 0: Read your prompt FILE in full")
    stop = text.index("### Step 1: Read the task description fully", start)
    return [block for block in _HASH_FENCE_RE.findall(text[start:stop])]


def _crlf_prompt_run_dir(tmp_path: Path) -> tuple[Path, Path]:
    """A run dir holding one casting prompt written with CRLF line endings."""
    run_dir = tmp_path / foundry_state.ARCHIVE_DIR / "d108-crlf"
    castings = run_dir / "castings"
    castings.mkdir(parents=True, exist_ok=True)
    prompt = castings / "casting-9-prompt.md"
    prompt.write_bytes(_CRLF_PROMPT)
    assert b"\r\n" in prompt.read_bytes(), (
        "the fixture lost its CRLF endings, so this test can no longer tell "
        "the byte spelling from the text spelling -- they agree on every other "
        "file."
    )
    return run_dir, prompt


def test_the_documented_hash_command_is_the_one_the_gate_accepts(
    tmp_path: Path,
) -> None:
    """D-108 / FR-019: run the published command, hand it to the door.

    ``check_reported_prompt_hash`` is the shared rung both
    Foundry-Accept-Casting and Foundry-Fix call, so accepting the documented
    command's output here is acceptance at both doors at once.
    """
    blocks = _documented_hash_commands()
    programs = [
        m.group(1)
        for m in (re.search(r'python3 -c "(.+?)"', b, re.S) for b in blocks)
        if m
    ]
    assert programs, (
        "teammate.md's Step 0 no longer publishes a runnable hash command "
        "(FR-019). A teammate told to state a hash and given no way to derive "
        "one copies it out of the dispatch message, which is precisely the "
        "case the check exists to detect."
    )

    run_dir, prompt = _crlf_prompt_run_dir(tmp_path)
    for program in programs:
        proc = subprocess.run(
            [sys.executable, "-c", program, str(prompt)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert proc.returncode == 0, (
            f"teammate.md's documented hash command failed to run: "
            f"{proc.stderr.strip()!r}. A command a teammate cannot execute is "
            f"a command it will work around."
        )
        reported = proc.stdout.strip()
        refusal = foundry_handoff.check_reported_prompt_hash(run_dir, 9, reported)
        assert refusal is None, {
            "why": (
                "the command agents/teammate.md publishes produces a hash the "
                "gate REFUSES on a file with CRLF line endings. A teammate "
                "that follows its own protocol exactly is refused and cannot "
                "read its way out: the refusal tells it to re-read the file "
                "and state the hash again, which reproduces the same rejected "
                "value every time. Fix whichever side moved -- the digest is "
                "over the file's BYTES -- never this assertion."
            ),
            "documented_command_produced": reported,
            "refusal": refusal,
        }
        assert reported == foundry_handoff._hash_file(prompt), (
            "the documented command and the package's own byte-hash helper "
            "disagree. check_reported_prompt_hash's docstring claims 'there is "
            "exactly one spelling in the package'; a disagreement here is that "
            "claim becoming false again."
        )


def test_teammate_says_the_digest_is_over_the_bytes(tmp_path: Path) -> None:
    """D-108: the reason, not just the recipe.

    The command alone is a ritual a reader can substitute an equivalent for --
    and the obvious equivalent, reading the file as text first, is the wrong
    one. So the file states the property the command has, names the
    text-reading spellings that break it, and names the refusal that results,
    which is what lets a teammate recognise the failure when it happens rather
    than looping on the refusal's own advice.
    """
    flat = _flat(TEAMMATE)
    assert "The digest is over the file's BYTES" in flat, (
        "teammate.md no longer states that the prompt digest is over the "
        "file's bytes. The text and byte spellings agree on every file "
        "containing no CR, so a reader who substitutes a text read sees no "
        "difference until a spec quotes a CRLF line."
    )
    assert "sha256sum" in flat, (
        "teammate.md no longer offers the sha256sum equivalent. A teammate on "
        "a box where the python spelling is awkward needs a second way to the "
        "same digest, and inventing one is how the text spelling comes back."
    )
    assert "Never hash the prompt as text" in flat, (
        "teammate.md no longer rules out hashing the prompt as text -- the "
        "single substitution that reproduces D-108."
    )
    assert "`stale_prompt_hash`" in flat, (
        "teammate.md no longer names the refusal a wrong spelling produces, so "
        "a teammate meeting it has no way to connect it to how it hashed."
    )


# ---------------------------------------------------------------------------
# D-110 / FR-041 -- the account is in the checklist a teammate actually copies
# ---------------------------------------------------------------------------


def _completion_message_checklist() -> str:
    """agents/teammate.md's Step 11 'Include in the completion message' list."""
    text = _read(TEAMMATE)
    start = text.index("### Step 11: Mark task complete with citations")
    stop = text.index("### Step 12:", start)
    return " ".join(text[start:stop].split())


def test_teammate_completion_checklist_names_the_failing_then_passing_account() -> None:
    """FR-041 / D-110: stated once, in the wrong place, is not stated.

    Foundry-Fix correctly refuses to take the failing-then-passing account as
    an argument, so the completion report is the only place it can live. The
    file said so -- inside the Step 7.5 LATENT-lane paragraph -- and the Step
    11 'Include in the completion message' enumeration, which is the checklist
    a teammate actually copies and which does carry bolded (required) bullets
    for the prompt hash, the citations and the evidence files, never named it.
    The requirement existed and was absent from the surface that is
    operationally consumed.

    So the assertion is SCOPED TO THE CHECKLIST. A whole-file substring check
    would have been green throughout the gap -- the sentence was always in the
    file -- which is exactly how the gap survived.
    """
    checklist = _completion_message_checklist()
    assert "**The failing-then-passing account, for every fix (required in GRIND).**" in checklist, (
        "teammate.md's Step 11 completion-message list does not require the "
        "failing-then-passing account (FR-041). Stating it only in the Step 7 "
        "LATENT-lane paragraph puts it outside the list a teammate copies when "
        "it writes the report, and Foundry-Fix cannot take it as an argument, "
        "so it lands nowhere."
    )
    assert "the test failed at" in checklist and "and passes at" in checklist, (
        "the Step 11 bullet gives no SHAPE for the account, so the requirement "
        "is satisfiable by any sentence mentioning a test. FR-041 wants the "
        "statement: red before the change, green after it, both commits named."
    )
    assert "silently DROPPED rather than refused" in checklist, (
        "the Step 11 bullet no longer says why the account cannot go in the "
        "Foundry-Fix call. Without the reason a teammate reads the bullet as "
        "duplication of a field it already passed and drops one of the two."
    )


# ---------------------------------------------------------------------------
# D-209 / FR-007 / CT-002 -- a documented filing row is a call the door ACCEPTS
# ---------------------------------------------------------------------------
#
# Two pins above already drove the documented rows, and both drove a PARTIAL
# door. `test_every_documented_latent_example_survives_the_filing_door` calls
# `validate_defect_filing`, which reads `tier`, `class` and
# `reproduction_attempted` and nothing else;
# `test_every_published_type_is_a_vocabulary_member` compares one field against
# one vocabulary. Neither ever asked whether the row would be ACCEPTED, and the
# advertised schema requires four fields, not three.
#
# So `agents/coverage-diff.md` published a report block in which every row was
# refused. That file was edited TWICE in this run (14210f3 "the fifth filing
# stream learns tier and class", 5c69e05 "a stream's own example files a type
# its door accepts"), gained `class`, `tier`, `target_kind`, a third row
# carrying LATENT plus `reproduction_attempted`, and two paragraphs asserting
# that class and tier are required on every defect -- and every row still
# carried no `description`, which `findings[].required` has listed all along.
# Driven at 916c1ca through `server.call_tool("Foundry-Sync", ...)`, each of
# the three rows verbatim:
#
#     Foundry-Sync refused - unusable argument(s): findings[0].description -
#     required, and absent; findings[0].source - required, and absent.
#
# and `defects.json` stayed empty. CT-002's errors cell is what turns that into
# a lost cycle rather than a lost row: one refused finding discards the whole
# batch, so the shape a stream copies from its own instructions takes the
# stream's other findings down with it.
#
# `source` was missing from the documented rows of ALL SIX filing surfaces, not
# just coverage-diff's -- the class is `filing-surface-prose-omits-a-field-its-
# own-door-requires` and it was corpus-wide. The five agent surfaces now name
# their own wire id on every row, which is also the only place a pin can READ
# it from: four of them declare a stream id nowhere else in the file, so a pin
# that supplied `source` itself would be supplying it from a hand-typed
# per-file table -- a second copy of a closed vocabulary, which is the failure
# `_EXPECTED_TYPE_ENUM` and `DEFECT_FILING_AGENTS` both exist to avoid.
#
# WHY THIS IS DRIVEN AND NOT A FIELD-PRESENCE CHECK
# -------------------------------------------------
# A presence check derived from the schema catches an absent field and stops
# there. The door is a ladder: house schema validation, then `source` against
# DEFECT_SOURCE_IDS, then `type` against DEFECT_TYPES, then the shared filing
# validator's tier/class/LATENT/denylist rungs. Driving the row through
# `server.call_tool` -- the same entry the SDK uses, with the server's own
# validation in place of the SDK's (D-042) -- puts every rung of that ladder
# behind this pin at once, and does it against the schema a client is actually
# served rather than against a re-reading of the module source.


def _documented_filing_rows(path: Path) -> list[dict]:
    """Every entry of every `defects` array in a file's normative examples.

    Malformed blocks read as "no rows" rather than raising, exactly as
    ``_documents_a_defects_array`` treats them -- several surfaces fence
    illustrative fragments and placeholder sketches as ```json, and one of
    those must not take this module down at collection time. The silence is
    safe only because ``test_the_driven_filing_roster_is_not_vacuous`` below
    asserts the known carriers still yield rows.
    """
    rows: list[dict] = []
    try:
        records = _example_records(path)
    except (json.JSONDecodeError, AssertionError):
        return rows
    for record in records:
        entries = record.get("defects")
        if isinstance(entries, list):
            rows.extend(row for row in entries if isinstance(row, dict))
    return rows


def _sync_required_fields() -> frozenset[str]:
    """What `Foundry-Sync` requires on a finding, per the ADVERTISED schema.

    Read from ``list_tools()`` rather than re-typed here, so the day the door
    requires a fifth field every documented row that lacks it fails HERE --
    which is the whole of D-209's second axis. The array is located by the
    same shape ``test_spec_ref_is_a_real_parameter_on_every_filing_surface``
    locates it by: the findings array is spelled `findings` on the wire while
    the prose and the ledger both call the records defects.
    """
    items = _tool_schema("Foundry-Sync")["properties"]["findings"]["items"]
    required = frozenset(items.get("required", ()))
    assert required, (
        "Foundry-Sync's findings item advertises no `required` array. Either "
        "the obligation moved (in which case this derivation must follow it) "
        "or it was dropped, which is CT-002's contract going away silently."
    )
    return required


#: Filing surfaces whose documented rows are NOT driven, mapped to the one
#: required field their rows omit.
#:
#: This is a DEBT LEDGER, not a licence: every entry is a live instance of the
#: D-209 class sitting in a file the casting that found it may not write.
#: ``test_the_undriven_surfaces_still_earn_their_exemption`` asserts each entry
#: is still refused AND still refused for exactly the recorded field, so an
#: exempt surface cannot quietly break some other way, and the day someone adds
#: the missing field the exemption test goes RED telling them to delete the
#: entry. An empty mapping is the finished state.
_UNDRIVEN_FILING_SURFACES = {
    SIGHT_SKILL: "source",
}

#: The surfaces whose rows are driven: the derived filing roster minus the
#: debt ledger. Derived on both sides -- a new filing surface joins by
#: documenting a `defects` array, and it is driven from that moment unless
#: someone puts it in the ledger above and says which field it is missing.
DRIVEN_FILING_SURFACES = tuple(
    path for path in DEFECT_FILING_AGENTS if path not in _UNDRIVEN_FILING_SURFACES
)


def _sync_one(row: dict, project_root: str) -> dict:
    """Drive one documented row through `Foundry-Sync` and return the result.

    Through ``server.call_tool``, because that is the door D-209 was driven at
    and the only one that runs the house schema validation the SDK's copy was
    turned off for (D-042). The result is recovered the way the four width
    surfaces tell a stream to recover one -- partition on
    ``display.RESULT_JSON_MARKER``, ``json.loads`` the remainder -- rather than
    by reading the formatter's internals.
    """
    from foundry_mcp import server as foundry_server

    blocks = asyncio.run(
        foundry_server.call_tool("Foundry-Sync", {"cycle": 1, "findings": [row]})
    )
    text = "\n".join(getattr(block, "text", str(block)) for block in blocks)
    _, marker, tail = text.partition(_display.RESULT_JSON_MARKER)
    return json.loads(tail if marker else text)


def _persisted_defects(project_root: str) -> list[dict]:
    """The records the scratch run's defect ledger actually holds.

    Read off disk rather than out of the door's return value, because the
    return counts what the door BELIEVES it wrote (`added`) and says nothing
    about the shape of it. D-213 is about a field that survives the write
    verbatim and is wrong -- `source` -- so the only place the claim can be
    checked is the record the next reader of this run will read.
    """
    run_dir = foundry_state.get_run_dir(project_root)
    assert run_dir is not None, (
        "the scratch run has no run directory, so nothing below can read a "
        "persisted record. The `sync_door` fixture initialises one; if that "
        "stopped working every driven pin here is testing an empty ledger."
    )
    ledger = run_dir / "defects.json"
    if not ledger.exists():
        return []
    payload = json.loads(ledger.read_text(encoding="utf-8"))
    records = payload.get("defects") if isinstance(payload, dict) else payload
    return [r for r in (records or ()) if isinstance(r, dict)]


@pytest.fixture
def sync_door(tmp_path: Path, monkeypatch):
    """A scratch run with `_project_root` pointed at it, torn down after.

    The run is thrown away with ``tmp_path``, so a driven row never touches
    the archive of the run these tests are executing inside.
    """
    from foundry_mcp import server as foundry_server
    from foundry_mcp.tools.foundry import foundry_init

    foundry_init(project_root=str(tmp_path))
    monkeypatch.setattr(foundry_server, "_project_root", str(tmp_path))
    try:
        yield str(tmp_path)
    finally:
        foundry_state.clear_active_run()


def test_the_driven_filing_roster_is_not_vacuous() -> None:
    """Floor check: a sweep over an empty roster asserts nothing.

    ``_documented_filing_rows`` swallows an unparseable block by design, so a
    fence that stopped parsing looks identical to a file with no rows -- and
    the pins below would go green on a corpus where every example had been
    deleted. This is the floor that fails first, and it names the five agent
    surfaces whose report shape is the thing being pinned.
    """
    carriers = {_rel(p) for p in DRIVEN_FILING_SURFACES if _documented_filing_rows(p)}
    expected = {_rel(p) for p in (*STREAM_AGENTS, COVERAGE_DIFF)}
    missing = sorted(expected - carriers)
    assert not missing, (
        f"{missing} yield no documented filing rows any more. Either the "
        f"report shape lost its `defects` array -- which is itself the defect, "
        f"since the shape a stream copies is the only thing that teaches it "
        f"what to file -- or the example stopped parsing as JSON. Fix the "
        f"file; never narrow this roster to match it."
    )


@pytest.mark.parametrize("path", DRIVEN_FILING_SURFACES, ids=_rel)
def test_every_documented_filing_row_carries_every_required_field(path: Path) -> None:
    """D-209 axis 1, said in the failure message the door cannot say.

    The driven pin below is the stronger check and would catch this too, but
    it reports whichever rung answered first. This one names the field and the
    file directly, and it is derived from the advertised schema, so a fifth
    required field lands here the moment the door declares it.
    """
    required = _sync_required_fields()
    gaps = {
        i: sorted(required - set(row))
        for i, row in enumerate(_documented_filing_rows(path))
        if required - set(row)
    }
    assert not gaps, (
        f"{_rel(path)} documents filing row(s) missing required field(s): "
        f"{gaps}. `Foundry-Sync` advertises {sorted(required)} as required on "
        f"every finding and refuses the WHOLE batch when one finding fails, so "
        f"a stream that copies this shape loses the findings beside it too. "
        f"Add the field to the EXAMPLE; never relax this assertion."
    )


@pytest.mark.parametrize("path", DRIVEN_FILING_SURFACES, ids=_rel)
def test_every_documented_filing_row_lands_at_the_door(path: Path, sync_door) -> None:
    """D-209 / FR-007 / CT-002: the shape a stream copies is ACCEPTED.

    Every row is driven VERBATIM -- nothing added, nothing renamed. That is
    the property being pinned: a stream that files its own instructions'
    example must not meet a refusal naming a field those instructions never
    taught it.

    TRUE POSITIVES THIS KEEPS (each is a rung of the door's ladder, and each
    fails here naming the file):
      - a row missing `description`, `source`, `tier` or `class` -- the whole
        advertised `required` array, not just the field D-209 was filed for;
      - a row whose `source` is not a `DEFECT_SOURCE_IDS` member;
      - a row whose `type` is not a `DEFECT_TYPES` member (D-174's class,
        driven rather than pattern-matched);
      - a LATENT row with no `reproduction_attempted`, or one the placeholder
        predicate refuses ("n/a", "none", "tbd");
      - a row a surface teaches as LATENT whose prose asserts a security
        property, which the denylist refuses as SECURITY_PROPERTY_CLAIM;
      - any field the door starts requiring after today.

    AND, since D-213, what the row PERSISTED AS. Landing is not the whole
    claim: a `source` that is a legal member of `DEFECT_SOURCE_IDS` but the
    wrong one for the dispatch reading the row lands cleanly and is stored
    verbatim, because the door refuses an unattributed finding and has no way
    to know which of eleven legal identities the caller actually is. Every
    single-dispatch surface still drives its one row and still has it recorded
    under the one id it names; what the read-back adds is that the id in the
    ledger is the id the example declared, for each row separately -- which is
    the only assertion a dual-dispatch surface's two rows can both satisfy.
    """
    rows = _documented_filing_rows(path)
    for i, row in enumerate(rows):
        result = _sync_one(row, sync_door)
        refused = bool(result.get("error")) or bool(result.get("refusals"))
        assert not refused, {
            "file": _rel(path),
            "row_index": i,
            "row_keys": sorted(row),
            "why": (
                "this surface documents a filing row that `Foundry-Sync` "
                "REFUSES. A stream copying its own instructions' shape has its "
                "whole batch discarded and hears about it as a refusal naming "
                "a field the instructions never mentioned. Fix the EXAMPLE (or "
                "the door), never this assertion."
            ),
            "error": result.get("error"),
            "missing_fields": result.get("missing_fields"),
            "invalid_fields": result.get("invalid_fields"),
            "refusals": result.get("refusals"),
        }
        persisted = _persisted_defects(sync_door)
        assert len(persisted) == i + 1, {
            "file": _rel(path),
            "row_index": i,
            "records_in_ledger": len(persisted),
            "why": (
                "the door reported no refusal and the ledger did not grow by "
                "exactly one record. A row that is neither refused nor "
                "recorded is the silent half of the same failure: the stream "
                "reads `ok` and its finding is not in defects.json for the "
                "lead to convert into a GRIND task."
            ),
            "result": result,
        }
        assert persisted[-1].get("source") == row.get("source"), {
            "file": _rel(path),
            "row_index": i,
            "declared_source": row.get("source"),
            "persisted_source": persisted[-1].get("source"),
            "why": (
                "the row landed and was recorded under a different `source` "
                "than it declares. The door does not coerce any more, so this "
                "is a divergence between the example and the ledger rather "
                "than the old rewrite-to-trace bug -- fix whichever side is "
                "wrong, never this assertion."
            ),
        }


def test_the_undriven_surfaces_still_earn_their_exemption(sync_door) -> None:
    """The debt ledger is checked, not trusted.

    An exemption list nobody re-tests is how a known gap becomes a permanent
    one. Each entry must still be refused -- otherwise the surface was fixed
    and the entry is stale -- and must still be refused for EXACTLY the
    recorded field, so an exempt surface that breaks some other way is not
    covered by an exemption written for a different reason.
    """
    for path, field in _UNDRIVEN_FILING_SURFACES.items():
        rows = _documented_filing_rows(path)
        assert rows, (
            f"{_rel(path)} is on the undriven ledger but documents no filing "
            f"rows at all. Delete the entry: there is nothing left to exempt."
        )
        for i, row in enumerate(rows):
            result = _sync_one(row, sync_door)
            assert result.get("missing_fields") == [f"findings[{0}].{field}"], (
                f"{_rel(path)} row {i} no longer refuses for exactly "
                f"{field!r} (missing_fields={result.get('missing_fields')!r}, "
                f"error={result.get('error')!r}). If the row now LANDS, delete "
                f"the `_UNDRIVEN_FILING_SURFACES` entry so the row joins "
                f"DRIVEN_FILING_SURFACES. If it refuses for something else, "
                f"that is a second defect the exemption was never written to "
                f"cover -- file it rather than widening the entry."
            )


# ---------------------------------------------------------------------------
# D-213 / FR-007 -- a DUAL-DISPATCH surface documents a row for EACH identity
# ---------------------------------------------------------------------------
#
# D-209 gave every documented filing row a `source`, and for four of the five
# agent surfaces one literal was the whole answer: each is dispatched under
# exactly one wire id, so the id its example carries is right every time the
# example is copied. `agents/assayer.md` is not one of those four. It is
# dispatched TWICE -- as the F2 PROVE stream (its Step 4 marks the stream
# complete with `stream: "prove"`, its width comes from
# `inspect_mode.prove_sample`, and its ledger is `progress/prove.jsonl`) and
# as the F4 ASSAY agent -- and both of its documented rows read
# `"source": "assay"`, with no sentence anywhere in the file telling a PROVE
# dispatch to substitute its own id.
#
# Driven at cdb9322: the two rows parsed out of the file and passed VERBATIM to
# `server.call_tool("Foundry-Sync", ...)` each returned `ok` with `added: 1` and
# persisted `source: "assay"`, while `Foundry-Stream(stream="prove")` succeeded
# in the same cycle -- one stream's work recorded under two identities, in two
# artifacts of the same run. That is the mis-attribution `foundry_sync_defects`
# stopped COERCING and now refuses; it arrives here through the one channel the
# refusal cannot see, because the wrong value is a legal `DEFECT_SOURCE_IDS`
# member. The door can refuse an unattributed finding. It cannot know which of
# eleven legal identities the caller actually is, so the example is the last
# place the question is answerable, and a literal that is right for one of two
# dispatches answers it wrongly half the time.
#
# WHY THE IDENTITIES ARE DERIVED AND NOT LISTED
# ---------------------------------------------
# A `{ASSAYER: ("prove", "assay"), ...}` table beside this test would be a
# second copy of a closed vocabulary -- the failure `_EXPECTED_TYPE_ENUM`,
# `DEFECT_FILING_AGENTS` and `DRIVEN_FILING_SURFACES` each exist to avoid --
# and it is worse here than usual: the table is maintained by whoever ALREADY
# knows a surface is dual-dispatch, and the surface that forgot is the surface
# nobody adds to it. So the identities are read out of each file's own text,
# from the three places a surface states a wire id as ITS OWN: the progress
# ledger it is told to write, the `stream:` argument it is told to mark itself
# complete under, and the "wire id `x`" phrase its ledger rule uses. A surface
# naming ANOTHER stream's id in one of those places is a defect in the other
# direction, so every id these yield is an identity the file is dispatched as.
# The roster swept is `DRIVEN_FILING_SURFACES`, the same one declaration the
# driving pin above sweeps -- there is no second list of surfaces here either.

#: The three places a filing surface states a wire id as ITS OWN identity.
#: Each is a self-identifying instruction rather than a mention: a file does
#: not tell its reader to write ANOTHER stream's ledger, mark ANOTHER stream
#: complete, or call ANOTHER stream's id "your wire id".
_LEDGER_ID_RE = re.compile(r"progress/([a-z0-9_]+)\.jsonl")
_STREAM_ARG_RE = re.compile(r"stream`?\s*[:=]\s*[\"']([a-z0-9_]+)[\"']")
_WIRE_ID_RE = re.compile(r"wire id[^`\n]{0,12}`([a-z0-9_]+)`")


def _declared_dispatch_ids(path: Path) -> frozenset[str]:
    """Every `DEFECT_SOURCE_IDS` member a surface claims as its own identity.

    Filtered through the vocabulary READ from the module rather than a set
    typed here, so a phrase that matches one of the regexes but names nothing
    the ledger accepts contributes nothing, and a member the vocabulary gains
    is covered the moment a surface starts claiming it.
    """
    text = _read(path)
    found = (
        set(_LEDGER_ID_RE.findall(text))
        | set(_STREAM_ARG_RE.findall(text))
        | set(_WIRE_ID_RE.findall(text))
    )
    return frozenset(found & vocab.DEFECT_SOURCE_IDS)


def _documented_sources(path: Path) -> frozenset[str]:
    """The `source` values a surface's own documented filing rows declare."""
    return frozenset(
        row["source"].strip()
        for row in _documented_filing_rows(path)
        if isinstance(row.get("source"), str) and row["source"].strip()
    )


def test_every_driven_surface_declares_its_own_dispatch_identity() -> None:
    """Floor check: a surface deriving no identity asserts nothing below.

    The parametrised check that follows is vacuously green on a file whose
    three signals all stopped matching -- which is indistinguishable, from
    inside that check, from a file that legitimately claims no identity. Every
    member of the driven roster states its wire id somewhere in its own
    instructions today, so "all of them yield at least one" is the floor, and
    it is derived on both sides: no roster is typed here, and no id is either.
    """
    silent = sorted(
        _rel(path) for path in DRIVEN_FILING_SURFACES if not _declared_dispatch_ids(path)
    )
    assert not silent, (
        f"{silent} no longer state a wire id of their own anywhere this "
        f"module can read one -- not as a `progress/<id>.jsonl` ledger, not as "
        f"a `stream: \"<id>\"` argument, not as a \"wire id `<id>`\" phrase. "
        f"Either the file stopped telling its dispatch who it is (which is "
        f"itself the defect: `Foundry-Liveness` looks a stream up under that "
        f"id) or the derivation drifted off the wording. Fix whichever it is; "
        f"never shrink this roster, and never replace the derivation with a "
        f"per-file table."
    )


@pytest.mark.parametrize("path", DRIVEN_FILING_SURFACES, ids=_rel)
def test_every_dispatch_identity_has_a_documented_filing_row(path: Path) -> None:
    """D-213: a file dispatched under N identities documents a row for each.

    The driving pin above proves every documented row LANDS and is recorded
    under the source it declares. That is the whole answer for a surface with
    one dispatch and no answer at all for a surface with two: `assay` lands
    perfectly well when a PROVE dispatch files it, and the ledger then carries
    a row the PROVE stream filed under the ASSAY agent's name. This is the
    other direction -- every identity the file claims must have a row that a
    dispatch reading as that identity can copy without editing the field.

    A single-dispatch surface passes exactly as before: its one declared id is
    the one its rows already carry. Only a file that grew a second dispatch,
    or lost one identity's row, fails here -- and it fails naming the id.
    """
    declared = _declared_dispatch_ids(path)
    documented = _documented_sources(path)
    unserved = sorted(declared - documented)
    assert not unserved, (
        f"{_rel(path)} is dispatched as {unserved} and documents no filing "
        f"row carrying that `source` -- its rows declare {sorted(documented)}. "
        f"A dispatch reading this file as {unserved[0]!r} copies the example "
        f"it is given, and the row LANDS: every value here is a legal member "
        f"of vocab.DEFECT_SOURCE_IDS, so nothing refuses it and the finding is "
        f"persisted under an identity that did not do the work, beside a "
        f"`Foundry-Stream` record filed under the identity that did. Add the "
        f"missing dispatch's row to the file (the rows differ in `source` and "
        f"nothing else); never narrow this assertion to the identity that "
        f"already has one."
    )


# ===========================================================================
# fallout AC-031 / AC-033 / AC-037 / AC-040 / AC-048 / FR-010 / FR-023 /
# FR-024 / FR-025 / FR-048 / FR-049 / FR-050 / FR-051 / GI-003 / GI-016 /
# NFR-002 / NFR-003 / NFR-011 / OT-029 / OT-037
#
# THE AGENT RECORDS; THE LEAD ONLY CONFIRMS. Casting 6 writes the NON-PROVE
# half of four rulings the run splits across two castings, and pins it here.
# The PROVE agent and the three producing skills state the same rulings in
# their own registers and are pinned in `tests/test_skill_prose.py`; nothing
# in this section reaches a skill file or `agents/assayer.md`.
# ===========================================================================

SPEC_TEST_DERIVER = AGENTS / "spec-test-deriver.md"

#: Every spelling of a stream id that can appear in an agent file, mapped to
#: the WIRE id `Foundry-Stream` actually takes. Built from the two vocabulary
#: constants in the `_PYTEST_DISCOVERY_PHRASE` shape rather than typed out, so
#: a stream added to `vocab` cannot leave this roster behind. That is the whole
#: difference between a pin that fails on the file someone forgot and a pin
#: that only ever checks the files it was born knowing.
_STREAM_SPELLING_TO_WIRE = {
    **{wire: wire for wire in vocab.STREAM_WIRE_IDS},
    **{canonical: wire for wire, canonical in vocab.WIRE_TO_CANONICAL.items()},
}

#: The symbol name the prose must cite for the stream vocabulary, recovered
#: from the module rather than re-typed: rename the constant in `vocab.py` and
#: this expected cite moves with it, so the prose that still names the old
#: spelling fails HERE instead of rotting into a cite that resolves to nothing.
_STREAM_VOCAB_SYMBOL = next(
    name
    for name, value in sorted(vars(vocab).items())
    if value is vocab.STREAM_WIRE_IDS
)

_STREAM_VOCAB_CITE = (
    "plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py#"
    + _STREAM_VOCAB_SYMBOL
)


def _declared_stream_wire_ids(path: Path) -> frozenset[str]:
    """The stream wire id(s) an agent file declares as ITS OWN.

    Two declarations count and NEITHER is the clause this section pins. A
    roster derived from the clause would drop the file that lost it and pass --
    a pin that cannot fail on the one regression it exists to catch, which is
    the D-017 shape in its purest form:

      * ``progress/<wire>.jsonl`` -- the liveness ledger, named for the wire id
        by an absolute the three ledger-bearing agents already state.
      * ``"stream": "<spelling>"`` -- the output shape's own field, which
        `coverage-diff` and `spec-test-deriver` carry instead of a ledger, the
        deriver under the CANONICAL spelling rather than the wire one.
    """
    text = _read(path)
    return frozenset(
        wire
        for spelling, wire in _STREAM_SPELLING_TO_WIRE.items()
        if f"progress/{wire}.jsonl" in text or f'"stream": "{spelling}"' in text
    )


#: The stream-producing AGENT files, DERIVED, minus the PROVE agent.
#:
#: Revision 3 splits the nine producers fallout AC-031 names across two
#: castings: the PROVE agent and the trace, prove and sight skills state the
#: ruling in their own registers and are pinned elsewhere. The exclusion here
#: is by WIRE ID -- `prove` -- and never by filename, so renaming the assayer
#: cannot quietly pull it into this roster and demand a paste that belongs to
#: another module.
NON_PROVE_STREAM_AGENTS = tuple(
    sorted(
        (
            path
            for path in AGENTS.glob("*.md")
            if (_declared := _declared_stream_wire_ids(path))
            and "prove" not in _declared
        ),
        key=_rel,
    )
)


def test_the_non_prove_stream_agent_roster_is_derived() -> None:
    """Floor check: every stream-recording pin below sweeps this roster.

    A derived roster buys nothing if the derivation silently narrows, and the
    narrowing is invisible at every assertion that reads it -- five green
    parametrisations over four files look exactly like five green
    parametrisations over five. So the five known members are asserted IN, and
    the PROVE agent is asserted OUT under the id that excludes it.
    """
    expected = {TRACER, FLOW_TRACER, RESEARCH_AUDITOR, COVERAGE_DIFF, SPEC_TEST_DERIVER}
    missing = sorted(_rel(p) for p in expected - set(NON_PROVE_STREAM_AGENTS))
    assert not missing, (
        f"{missing} no longer derive into NON_PROVE_STREAM_AGENTS. A file drops "
        f"out by losing the `progress/<wire>.jsonl` ledger path AND the "
        f"`\"stream\": \"<id>\"` field in its output shape -- either of which is "
        f"itself the defect, because a stream that names its wire id nowhere is "
        f"a stream `Foundry-Liveness` and the roll-up cannot find. Fix the file "
        f"rather than hard-coding this roster."
    )
    assert ASSAYER not in NON_PROVE_STREAM_AGENTS, (
        "agents/assayer.md derived into NON_PROVE_STREAM_AGENTS. It is the PROVE "
        "agent: its stream statement is casting 11's and is pinned in "
        "tests/test_skill_prose.py, so sweeping it here would pin one ruling in "
        "two modules that are free to drift apart."
    )
    assert "prove" in _declared_stream_wire_ids(ASSAYER), (
        "agents/assayer.md no longer declares the `prove` wire id, which is the "
        "only thing keeping it out of the roster above. Restore it in the "
        "assayer rather than excluding the file by name here."
    )


#: The clauses every non-PROVE stream agent states BYTE-IDENTICALLY. Each is
#: the load-bearing fragment of the ruling and never a whole sentence: the body
#: around it is each file's own voice, which is required -- pasting one
#: paragraph into five files was explicitly rejected, and the module docstring
#: says why. Parametrised across the roster so an edit that fixes three of the
#: five fails naming the two it forgot.
_STREAM_RECORDING_CLAUSES = (
    (
        "**You record your own stream; the lead only confirms the record exists.**",
        "the imperative that carries fallout GI-016 / OT-029: the AGENT records, "
        "and no lead prose records for it",
    ),
    (
        "Call `Foundry-Stream` yourself with `stream`, `cycle`, `items_checked`, "
        "`items_total` and `findings_count`",
        "the door and its five arguments; a clause naming the tool without its "
        "arguments leaves the agent to guess a call the boundary rejects",
    ),
    (
        "Take `cycle` from `Foundry-Next`",
        "the roll-up is keyed by the server's counter; an agent inventing a cycle "
        "records against one nothing reads",
    ),
    (
        "A second call for the same stream and cycle REPLACES the first, names in "
        "`replaced` what it replaced, and keeps every record under `records[]`",
        "fallout FR-023 / FR-049 replace semantics, stated on the agent side of "
        "the door casting 2 built; without it a re-run reads as double coverage",
    ),
    (
        "a stream that never records contributes nothing to the cycle's coverage "
        "roll-up, where its absence reads as no coverage rather than as a broken "
        "call",
        "the closing absolute the trace register uses: what the absence COSTS, "
        "which is what stops the clause reading as advice",
    ),
)


@pytest.mark.parametrize("path", NON_PROVE_STREAM_AGENTS, ids=lambda p: p.name)
@pytest.mark.parametrize("clause,why", _STREAM_RECORDING_CLAUSES, ids=lambda v: v[:44])
def test_each_non_prove_stream_agent_records_its_own_stream(
    path: Path, clause: str, why: str
) -> None:
    """fallout AC-031 / FR-023 / FR-049 / GI-016: the agent records, in its own file."""
    assert clause in _flat(path), (
        f"{_rel(path)} no longer states: {clause!r}. That clause is {why}. All "
        f"{len(NON_PROVE_STREAM_AGENTS)} non-PROVE stream agents must state it, "
        f"in their own voice around it -- an agent reading its own file and "
        f"finding no instruction to record waits for a lead that fallout OT-029 "
        f"forbids from recording, and the cycle's roll-up reads the silence as "
        f"no coverage."
    )


@pytest.mark.parametrize("path", NON_PROVE_STREAM_AGENTS, ids=lambda p: p.name)
def test_each_non_prove_stream_agent_cites_the_stream_vocabulary(path: Path) -> None:
    """fallout NFR-011: the prose points at the constant instead of copying it.

    The cite is BUILT from the module above, so renaming the constant fails
    here on every file that still names the old symbol rather than leaving five
    agent files citing a symbol that resolves to nothing.
    """
    assert _STREAM_VOCAB_CITE in _flat(path), (
        f"{_rel(path)} does not cite {_STREAM_VOCAB_CITE}. The stream ids are a "
        f"closed vocabulary with one home; an agent file that re-types the set "
        f"is a copy free to drift, and the drift surfaces as a door refusing a "
        f"`stream` value these instructions taught."
    )


#: Spellings that would put the recording duty back on the lead. Absence
#: assertions, because a positive pin cannot see prose ADDED beside it: a file
#: can state the imperative above and, four bullets later, tell the agent the
#: lead marks the stream complete -- both true to a substring check, and the
#: agent believes the second one.
_LEAD_RECORDS_SPELLINGS = (
    "the lead records it",
    "the lead will record",
    "the lead marks the stream",
    "the lead marks this stream",
    "the lead calls `foundry-stream`",
    "the lead records your stream",
)


@pytest.mark.parametrize("path", NON_PROVE_STREAM_AGENTS, ids=lambda p: p.name)
def test_no_stream_agent_says_the_lead_records_for_it(path: Path) -> None:
    """fallout GI-016 / OT-029, the absence half: no file re-delegates upward."""
    flat = _flat(path).lower()
    found = sorted(s for s in _LEAD_RECORDS_SPELLINGS if s in flat)
    assert not found, (
        f"{_rel(path)} states {found}, which hands the recording duty back to "
        f"the lead. fallout GI-016 puts it on the AGENT and the lead's own "
        f"imperative is confirm-the-record-exists; a file saying otherwise "
        f"produces the double-record the replace semantics were built to end."
    )


# ---------------------------------------------------------------------------
# fallout AC-033 / FR-024 / FR-050 -- read the roster, derive only if absent
# ---------------------------------------------------------------------------

#: The two stream agents that DERIVE their own item list rather than reading a
#: width or a manifest the server already recorded. Declared rather than
#: derived, in the disposition `STREAM_AGENTS` above already uses: the property
#: that separates them is WHERE the population comes from -- `research/` plus
#: the spec's Informational section for one, the spec's Contracts rows for the
#: other -- and that is a fact about the source material, not a string in the
#: file. The floor check below holds them inside the derived roster, so a file
#: that stops being a stream agent at all cannot sit here unnoticed.
ROSTER_DERIVING_AGENTS = (RESEARCH_AUDITOR, SPEC_TEST_DERIVER)


def test_the_roster_deriving_agents_are_stream_agents() -> None:
    """Floor check: the roster ruling only binds files that record a stream."""
    stray = sorted(
        _rel(p) for p in set(ROSTER_DERIVING_AGENTS) - set(NON_PROVE_STREAM_AGENTS)
    )
    assert not stray, (
        f"{stray} carry the roster ruling but no longer derive into "
        f"NON_PROVE_STREAM_AGENTS. The roster exists to make `items_total` "
        f"checkable on a stream RECORD; a file with no record has no use for one."
    )


_ROSTER_CLAUSES = (
    (
        "**Read the roster before you derive one.**",
        "fallout AC-033's imperative: the persisted list wins over a fresh "
        "derivation, which is what gives the numbering identity across cycles",
    ),
    (
        "call `Foundry-Roster(stream, items=[...])` at that first derivation",
        "the door and its two required arguments, named as the tool schema "
        "declares them",
    ),
    (
        f"a second write is refused `{rosters.ROSTER_EXISTS}` unless you pass "
        f"`revise=true` with a reason",
        "the write-once door; without it a silent rewrite drops the prior list "
        "and fallout GI-006 forbids exactly that",
    ),
    (
        f"`Foundry-Stream` refuses `{streams.ROSTER_MISMATCH}` when `items_total` "
        f"differs from the persisted roster's length",
        "the cross-door join: the agent must know why its own record depends on "
        "the roster it wrote, or it reports a shrunken population as full "
        "coverage",
    ),
)


@pytest.mark.parametrize("path", ROSTER_DERIVING_AGENTS, ids=lambda p: p.name)
@pytest.mark.parametrize("clause,why", _ROSTER_CLAUSES, ids=lambda v: v[:44])
def test_each_deriving_agent_states_the_roster_rule(
    path: Path, clause: str, why: str
) -> None:
    """fallout AC-033 / FR-024 / FR-050: one ruling, two voices, pinned once.

    The two refusal names are INTERPOLATED from the modules that declare them,
    so renaming either constant fails here on the prose that still spells the
    old one -- a prose statement and the door it describes cannot drift apart
    while this holds.
    """
    assert clause in _flat(path), (
        f"{_rel(path)} no longer states: {clause!r}. That clause is {why}. Both "
        f"deriving agents state it, each in its own voice around it; an auditor "
        f"that re-derives a list a roster already holds renumbers it silently, "
        f"and the regression check it owes has no stable prior state to read."
    )


@pytest.mark.parametrize("path", ROSTER_DERIVING_AGENTS, ids=lambda p: p.name)
def test_each_deriving_agent_names_its_own_roster_document(path: Path) -> None:
    """fallout NFR-011: the path is composed, not copied.

    Built from the ledger directory constant and the file's OWN declared wire
    id, so an agent naming another stream's roster -- or the directory under a
    name casting 1 does not use -- fails here rather than at a read that
    silently finds nothing.
    """
    wires = _declared_stream_wire_ids(path)
    assert len(wires) == 1, (
        f"{_rel(path)} declares {sorted(wires)} as its stream wire id(s). A "
        f"roster document is per stream; a file claiming two has no single "
        f"answer to which roster is its own."
    )
    expected = f"{rosters.ROSTERS_DIRNAME}/{next(iter(wires))}.json"
    assert expected in _read(path), (
        f"{_rel(path)} does not name `{expected}`, the document its roster "
        f"actually lives at. An agent told to read a roster it cannot name "
        f"re-derives every cycle, which is the state fallout FR-050 ends."
    )


def _forbidden_source_roots() -> frozenset[str]:
    """`FORBIDDEN_SOURCE_ROOTS`, read out of the validator without running it.

    Parsed with `ast` rather than imported: the module's filename carries a
    hyphen, and executing a script to read one constant out of it is a side
    effect this suite has no reason to take. Derived rather than re-typed for
    the usual reason -- a hand-copied denylist here would pass while the
    validator grew a root the absence assertion below never learned.
    """
    source = (
        REPO_ROOT / "plugins" / "foundry" / "scripts" / "validate-test-observations.py"
    ).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(t, ast.Name) and t.id == "FORBIDDEN_SOURCE_ROOTS"
            for t in node.targets
        ):
            continue
        call = node.value
        assert isinstance(call, ast.Call) and call.args, (
            "FORBIDDEN_SOURCE_ROOTS is no longer a frozenset({...}) literal; "
            "update this reader rather than re-typing the roots here."
        )
        return frozenset(ast.literal_eval(call.args[0]))
    raise AssertionError(
        "validate-test-observations.py declares no FORBIDDEN_SOURCE_ROOTS. The "
        "code-blind denylist is the constant the absence assertion below is "
        "derived from; it cannot be replaced with a typed copy."
    )


def _section(path: Path, heading: str) -> str:
    """One `## Heading` section of a markdown file, flattened.

    Absence assertions are scoped to the section that carries the ruling. A
    whole-file absence check cannot work on this file: § Code-Blind Discipline
    NAMES every forbidden root, which is the point of it.
    """
    text = _read(path)
    assert text.count(heading + "\n") == 1, (
        f"{_rel(path)} carries {text.count(heading)} `{heading}` headings; the "
        f"section pin needs exactly one."
    )
    start = text.index(heading + "\n")
    rest = text.index("\n## ", start + len(heading))
    return " ".join(text[start:rest].split())


def test_the_derivers_roster_clause_sanctions_no_implementation_source_read() -> None:
    """fallout GI-003 / NFR-003: the code-blind stream stays code-blind.

    The ABSENCE half, and it is the half that matters. A positive pin on "the
    items come from the Contracts table" stays green under a rewrite that adds
    "and read the implementing module to confirm the surface exists" beside it
    -- the sentence the positive pin quotes is still there, and TEST-01 has
    quietly stopped being code-blind. So the section that carries the roster
    ruling is asserted to name NO forbidden source root at all, against the
    denylist the validator itself enforces.
    """
    section = _section(SPEC_TEST_DERIVER, "## Roster")
    named = sorted(root for root in _forbidden_source_roots() if root in section)
    assert not named, (
        f"agents/spec-test-deriver.md's `## Roster` section names {named}, "
        f"root(s) on the code-blind denylist. Deriving, writing or reading a "
        f"roster is spec work: the items come from the spec's `## Contracts` "
        f"rows. A roster clause that reaches a source root gives TEST-01 the "
        f"one reason it has ever needed to read implementation source, and "
        f"fallout NFR-003 makes that a Locked constraint rather than a "
        f"preference."
    )
    assert "`## Contracts`" in section, (
        "agents/spec-test-deriver.md's `## Roster` section no longer says the "
        "items come from the spec's `## Contracts` rows. Without the positive "
        "half the absence above is satisfied by a section that says nothing "
        "about where the items come from at all."
    )
    assert "TEST_DERIVER_READ_SOURCE" in section, (
        "agents/spec-test-deriver.md's `## Roster` section no longer names the "
        "halt token. The rule needs the exit it forces when a derivation seems "
        "to want a source read, or the reader is left to decide."
    )


# ---------------------------------------------------------------------------
# fallout AC-048 / FR-025 -- the tracer marks fallout of an earlier fix
# ---------------------------------------------------------------------------

_FALLOUT_CLAUSES = (
    "**Set `fallout_of` when the finding is fallout of an earlier fix.**",
    "sibling surface left on a contract a previous cycle's fix changed is not a "
    "fresh defect",
    "An id the ledger does not carry is REFUSED at the door rather than stored",
)


@pytest.mark.parametrize("clause", _FALLOUT_CLAUSES, ids=lambda v: v[:44])
def test_the_tracer_states_the_fallout_marking_rule(clause: str) -> None:
    """fallout AC-048 / FR-025, the TRACE half.

    The PROVE half -- `agents/assayer.md` and the prove and trace skills -- is
    casting 11's and is pinned in its own module. Pinned on the tracer alone
    here, deliberately: sweeping the wider roster would demand the clause in
    four files whose castings have not written it, turning a green module red
    for prose nobody had a chance to add.
    """
    assert clause in _flat(TRACER), (
        f"agents/tracer.md no longer states: {clause!r}. Fallout marking is one "
        f"optional field with no default the server can supply -- `measure-run` "
        f"counts records carrying `fallout_of` per cycle, so an unmarked cycle "
        f"reads as a cycle that produced none, and the measurement this run "
        f"exists to make honest goes quiet instead of going red."
    )


# ---------------------------------------------------------------------------
# fallout AC-037 / AC-040 / FR-010 / FR-048 / FR-051 / OT-037 -- the builder's
# three: the sweep shell, the cross-casting concern door, the fix ledger
# ---------------------------------------------------------------------------

#: The refusal the sweep raises on a command it cannot parse. Spelled once here
#: because two assertions name it: the prose pin that the teammate was TOLD the
#: token, and the cross-door join below that the token is one the vocabulary
#: actually carries. Split across two modules they would be free to drift, and
#: the drift is invisible -- a teammate reading a token the sweep never emits
#: greps a refusal that cannot happen and concludes the rule is dead.
_SWEEP_SYNTAX_TOKEN = "EVIDENCE_COMMAND_SYNTAX"

_TEAMMATE_CLAUSES = (
    (
        "The server re-runs every `# evidence-cmd:` under `/bin/sh -c`",
        "fallout AC-037: the sweep shell, named. `Popen(cmd, shell=True)` with "
        "no `executable=` is `/bin/sh`, and a teammate authoring in another "
        "shell has no way to know that from prose that never says it",
    ),
    (
        "Parse it yourself with `/bin/sh -n` before you commit it.",
        "the teammate's own half of the same rule -- the parse that turns a "
        "sweep refusal into a local failure before the commit exists",
    ),
    (
        "the pre-commit guard lints the evidence logs you STAGED",
        "fallout FR-051's first door, scoped to STAGED logs so a peer's "
        "unstaged work in the shared tree cannot make it fire",
    ),
    (
        f"refuse the crossing with `{_SWEEP_SYNTAX_TOKEN}`",
        "fallout FR-051's second door: one rule with two enforcement points, "
        "stated as one rule so a teammate does not read it as two",
    ),
    (
        "**A concern that lands on ANOTHER casting goes through `Foundry-Concern`, "
        "not only into the file.**",
        "fallout FR-010: the structured ledger is the tool; concerns.md stays "
        "the prose rendering, which is what a teammate READS and never what the "
        "server parses",
    ),
    (
        "with `casting_id`, `cycle`, `target` (the casting id, key file or symbol "
        "it lands on) and `text`",
        "the four write-arm arguments, as the tool schema declares them -- a "
        "teammate inventing an argument name is refused at the boundary",
    ),
    (
        "an open cross-casting concern from the closing GRIND refuses "
        "`Foundry-Phase('inspect_start')`",
        "what leaving one open COSTS, which is the only thing that makes "
        "raising one early worth doing",
    ),
    (
        "**One `Foundry-Fix` acceptance line per dispatched defect id (required "
        "in GRIND).**",
        "fallout AC-040 / OT-037: the completion-report line, one per dispatched "
        "id, carrying what the door answered",
    ),
    (
        "it refuses `DISPATCHED_DEFECT_UNRECORDED`, naming every dispatched id "
        "still open whose file a commit since the cycle baseline SHA touched",
        "fallout FR-048: the door the report line keeps open, named by its "
        "refusal so the two halves of one rule cannot drift",
    ),
)


@pytest.mark.parametrize("clause,why", _TEAMMATE_CLAUSES, ids=lambda v: v[:44])
def test_teammate_states_the_builders_three_rulings(clause: str, why: str) -> None:
    """fallout AC-037 / AC-040 / FR-010 / FR-048 / FR-051 / OT-037.

    `agents/teammate.md` is the one file every builder reads, and all three
    rulings are things a builder learns there or does not learn at all: the
    shell the server will re-run its evidence command under, the door a
    cross-casting concern goes through, and the line that records a dispatched
    fix was accepted.
    """
    assert clause in _flat(TEAMMATE), (
        f"agents/teammate.md no longer states: {clause!r}. That clause is {why}."
    )


def test_the_teammate_fix_ledger_line_is_required_of_every_dispatched_id() -> None:
    """fallout AC-040 / OT-037: per ID, not per report.

    A report carrying one acceptance line for the cycle satisfies "the report
    mentions Foundry-Fix" and leaves four dispatched ids unaccounted for, which
    is the exact state `Foundry-Team-Down` then refuses on. The pin is on the
    per-id absolute rather than on the tool name.
    """
    flat = _flat(TEAMMATE)
    assert (
        "Write the line for every dispatched id, the ones you could not fix "
        "included, saying so." in flat
    ), (
        "agents/teammate.md's fix-ledger bullet lost the per-id absolute. "
        "Without it a single summary line reads as compliance, and the ids it "
        "omits are the ones whose ledger rows stay open while their fixes sit "
        "on the branch."
    )


# ---------------------------------------------------------------------------
# The cross-door joins: every refusal the prose above PROMISES, driven or read
# at the door that gives it. A prose statement and its door drift apart
# silently -- the file still reads correctly and the refusal it describes has
# been renamed, so the reader greps a token nothing emits and concludes the
# rule is dead. These are the assertions that make that drift loud.
# ---------------------------------------------------------------------------


def test_the_sweep_syntax_refusal_the_teammate_names_is_a_real_failure_token() -> None:
    """fallout AC-037 / FR-051: the token in the prose is the token in the vocabulary."""
    assert _SWEEP_SYNTAX_TOKEN in evidence_doors.KNOWN_EVIDENCE_FAILURE_TOKENS, (
        f"agents/teammate.md tells every builder the sweep refuses "
        f"`{_SWEEP_SYNTAX_TOKEN}`, and that is not a member of "
        f"evidence.KNOWN_EVIDENCE_FAILURE_TOKENS "
        f"({sorted(evidence_doors.KNOWN_EVIDENCE_FAILURE_TOKENS)}). The "
        f"allowlist is closed, so a token outside it is a refusal the sweep "
        f"cannot raise: the prose would be teaching a name nothing ever emits."
    )


def test_the_team_down_refusal_the_completion_report_names_is_the_doors_own() -> None:
    """fallout AC-040 / FR-048 / OT-037: the report line and the door that needs it."""
    assert (
        directives.DISPATCHED_DEFECT_UNRECORDED in _flat(TEAMMATE)
    ), (
        f"agents/teammate.md no longer names "
        f"`{directives.DISPATCHED_DEFECT_UNRECORDED}`, the refusal "
        f"`Foundry-Team-Down` gives when a dispatched id is open and a commit "
        f"since the cycle baseline touched its file. The completion-report line "
        f"is what keeps that door open; a teammate who cannot name the refusal "
        f"has no reason to believe the line is load-bearing."
    )


def test_the_unknown_fallout_id_the_tracer_promises_is_the_refusal_the_door_gives() -> None:
    """fallout AC-048 / FR-025 / CT-019: driven, not asserted from the prose.

    The tracer clause closes on "an id the ledger does not carry is REFUSED at
    the door rather than stored", which is a claim about the SERVER. Read off
    the prose it is unfalsifiable; driven here it is a joined pair, and the two
    negative cases keep the join honest -- a door that refused every value
    would satisfy a one-sided check while making the field unusable.
    """
    ledger = [{"id": "D-001"}]
    refusal = foundry_doors.fallout_parent_problem("D-999", ledger)
    assert refusal is not None, (
        "the filing door accepts `fallout_of: 'D-999'` against a ledger that "
        "holds no such id, while agents/tracer.md tells the TRACE stream the "
        "door refuses it. One of the two is wrong, and the prose is the half a "
        "stream reads before it files."
    )
    assert foundry_doors.fallout_parent_problem("D-001", ledger) is None, (
        "the door refuses a `fallout_of` naming an id the ledger DOES hold. "
        "The tracer is told to set the field on real parents; a door refusing "
        "those makes the rule unusable rather than strict."
    )
    assert foundry_doors.fallout_parent_problem("", ledger) is None, (
        "the door refuses an UNSET `fallout_of`. The field is optional by "
        "construction -- the tracer is told to leave it unset when a finding "
        "stands on its own -- so refusing the empty value would make every "
        "standalone finding unfilable."
    )
