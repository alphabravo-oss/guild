"""lead-stalls OT-008 / OT-009 / GI-002 / FR-009 / NFR-001 — the change stayed in bounds.

Two bounds live here, and they are different KINDS of claim, which is why the
second one is enforced so differently from the first.

THE HOOK SURFACE, which is a standing property of the tree
-----------------------------------------------------------
This release is a payload-and-prose fix. lead-stalls GI-002 states the bound in
four words — "No hook - payload fix only" — and names its own violation shape:
"Any new entry in `hooks.json`; any new file under `hooks/`". The reverted
4.12.0 attempt is why the bound is written down rather than assumed: it cost
585 lines across four files and leaned on a 637-line `park.py` for its
allow-condition, and this release exists because that machinery was not what
the problem needed.

WHY A RECORDED DIGEST AND NOT A LIVE `git show`
------------------------------------------------
lead-stalls OT-008 is "byte-identical to its PRE-CHANGE content", so the
comparison needs a pre-change fact, and a fact read out of the working tree
after editing began records whatever had already happened to it. The digest
below was taken from git at this run's base commit, `dda6154` — the committed
state before any casting of this run touched the tree — and is written here as
a literal. Shelling out to `git show dda6154:...` at test time would read the
same bytes but would fail in a shallow clone and in any checkout that does not
carry that commit, including the detached worktree the evidence gate
re-executes in. A literal is readable in all of them.

WHY `__pycache__` IS CARVED OUT OF THE ROSTER, BY NAME
-------------------------------------------------------
`plugins/foundry/hooks/` accumulates `__pycache__/*.pyc` in a working tree that
has run the hooks, and `.gitignore` ignores both patterns, so those files exist
here and do not exist in a `git worktree add --detach` checkout. A roster
comparison that counted them would fail in the shared tree and pass in the
gate's worktree — the same assertion returning two answers on two checkouts of
one commit, which is worse than no assertion. The carve-out is exactly the two
ignored patterns: a hook added as a `.sh`, a `.py`, or a new entry in
`hooks.json` is still caught.

THE SOURCE-FILE CAP, which is a claim about a DIFF
---------------------------------------------------
lead-stalls NFR-001 also caps how many source files this release may touch, and
that clause landed here unenforced: nothing in the suite counted source files,
so the build overran the cap in silence and the overrun was found by reading a
diff by hand. It was then overrun a second time, in the same silence, by rungs
that counted a hand-typed roster against a hand-typed cap — an arithmetic that
is true by construction whatever the run did (D-071). The cap is now MEASURED
from the change, with `git diff` against the same base commit the digest above
is pinned to, and the roster is asserted equal to what that measurement returns
rather than standing in for it. The rungs and the long note on why the earlier
recorded-diff ruling was wrong are at the bottom of this file.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath

import pytest

# tests/test_containment.py -> [0]=tests, [1]=mcp-server, [2]=foundry,
# [3]=plugins, [4]=repo-root. Mirrors test_release_version.py's precedent:
# the surface under containment lives ABOVE mcp-server/, so the plugin root is
# not enough.
REPO_ROOT = Path(__file__).resolve().parents[4]
HOOKS_DIR = REPO_ROOT / "plugins" / "foundry" / "hooks"
HOOKS_JSON = HOOKS_DIR / "hooks.json"

#: The run's base commit — the committed state this release's containment is
#: measured against. Recorded for the reader; the digest below is what the
#: assertions actually compare, so nothing here depends on the commit being
#: present in the checkout.
BASE_COMMIT = "dda615434dfb4624e1ab6328851afc91ef58e5e1"

#: sha256 of `git show dda6154:plugins/foundry/hooks/hooks.json`.
#: lead-stalls OT-008 is a claim about BYTES, so this is taken over the raw
#: blob and the file is read back in binary — a text read would fold CRLF to
#: LF before the digest saw it and would call two different files identical.
PRE_CHANGE_HOOKS_JSON_SHA256 = (
    "9bcb79f730fd704c3bf1f91c868ced8eccd4f6f5dd6961228329edac705b11a2"
)

#: Every tracked path under `plugins/foundry/hooks/` at BASE_COMMIT, relative
#: to that directory. lead-stalls OT-009 forbids an addition; equality is
#: asserted rather than containment because a hook file that DISAPPEARED is a
#: breach of the same bound from the other side, and equality reports it for
#: free with a message that names which direction moved.
PRE_CHANGE_HOOKS_ROSTER = frozenset(
    {
        "hooks.json",
        "pre-commit-guard.sh",
        "session-start-serena.sh",
    }
)  # 3 items

#: The two `.gitignore` patterns carved out of the roster walk, and nothing
#: else. See the module docstring for why these two and no others.
_IGNORED_DIR = "__pycache__"
_IGNORED_SUFFIX = ".pyc"


def _digest(payload: bytes) -> str:
    """sha256 of exactly the bytes handed in."""
    return hashlib.sha256(payload).hexdigest()


def _hooks_roster() -> frozenset[str]:
    """Every file under `hooks/` today, relative, minus the ignored patterns."""
    return frozenset(
        str(path.relative_to(HOOKS_DIR))
        for path in HOOKS_DIR.rglob("*")
        if path.is_file()
        and path.suffix != _IGNORED_SUFFIX
        and _IGNORED_DIR not in path.relative_to(HOOKS_DIR).parts
    )


def test_hooks_json_is_byte_identical_to_its_pre_change_content() -> None:
    """lead-stalls OT-008 verbatim, over the bytes rather than the parse."""
    assert HOOKS_JSON.is_file(), (
        f"lead-stalls OT-008 is measured against {HOOKS_JSON}, which is not in "
        f"the tree. A containment check that cannot find its subject reports "
        f"nothing; the file moved, or this path constant is stale."
    )

    actual = _digest(HOOKS_JSON.read_bytes())

    assert actual == PRE_CHANGE_HOOKS_JSON_SHA256, (
        f"{HOOKS_JSON.relative_to(REPO_ROOT)} reads sha256 {actual}, expected "
        f"{PRE_CHANGE_HOOKS_JSON_SHA256} — its content at base commit "
        f"{BASE_COMMIT[:7]}. lead-stalls GI-002 is 'No hook - payload fix "
        f"only', and lead-stalls OT-008 is byte-identity: this release may not "
        f"change this file at all. Revert the edit; put the behaviour on the "
        f"payload."
    )


def test_no_file_was_added_under_the_hooks_directory() -> None:
    """lead-stalls OT-009 verbatim, and lead-stalls GI-002's second shape."""
    assert HOOKS_DIR.is_dir(), (
        f"lead-stalls OT-009 is measured against {HOOKS_DIR}, which is not a "
        f"directory in this tree."
    )

    roster = _hooks_roster()
    added = sorted(roster - PRE_CHANGE_HOOKS_ROSTER)
    removed = sorted(PRE_CHANGE_HOOKS_ROSTER - roster)

    assert not added, (
        f"lead-stalls OT-009 forbids an addition under "
        f"{HOOKS_DIR.relative_to(REPO_ROOT)}; {len(added)} appeared: {added}. "
        f"lead-stalls FR-009 is 'No hook - payload fix only' — a new hook "
        f"script is the machinery this release was scoped to avoid."
    )
    assert not removed, (
        f"{len(removed)} file(s) vanished from "
        f"{HOOKS_DIR.relative_to(REPO_ROOT)}: {removed}. lead-stalls GI-002 "
        f"bounds this release away from the hook surface in both directions; "
        f"deleting a shipped hook is not 'payload fix only' either."
    )


def test_hooks_json_still_declares_exactly_the_one_session_start_hook() -> None:
    """lead-stalls GI-002's first shape — 'Any new entry in `hooks.json`'.

    The digest above already fails on any byte that moved, so this cannot fail
    alone. It runs as its own test so that a breach is reported TWICE and the
    second report says WHAT changed: a digest mismatch says the bytes differ
    and sends the reader to a diff, while this one names the event that was
    added.
    """
    declared = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))["hooks"]

    assert sorted(declared) == ["SessionStart"], (
        f"{HOOKS_JSON.relative_to(REPO_ROOT)} declares hook event(s) "
        f"{sorted(declared)}, expected ['SessionStart']. lead-stalls GI-002's "
        f"violation shape is 'Any new entry in hooks.json'; this release adds "
        f"no hook event."
    )

    commands = [
        hook["command"]
        for group in declared["SessionStart"]
        for hook in group["hooks"]
    ]
    assert commands == [
        "${CLAUDE_PLUGIN_ROOT}/hooks/session-start-serena.sh"
    ], (
        f"SessionStart runs {commands}; expected the one shipped "
        f"session-start script. lead-stalls GI-002 bounds this release away "
        f"from the hook surface, and a second command on the existing event is "
        f"a new hook wearing an old event's name."
    )


def test_the_digest_comparison_actually_bites() -> None:
    """The guard on the guard: prove a changed byte produces a changed digest.

    Every assertion above is an EQUALITY that passes when nothing moved, which
    is also what a broken comparison does. Without this, `_digest` could be
    stubbed to a constant, or `read_bytes` could be reading a path that no
    longer exists behind a helper that swallowed the error, and all three tests
    would stay green while measuring nothing. Driven on an in-memory mutation
    so the tree is never touched.
    """
    real = HOOKS_JSON.read_bytes()
    mutated = real + b"\n"

    assert _digest(real) == PRE_CHANGE_HOOKS_JSON_SHA256, (
        "the live file no longer matches the recorded digest, so this "
        "self-check cannot say whether the comparison works"
    )
    assert _digest(mutated) != PRE_CHANGE_HOOKS_JSON_SHA256, (
        "appending a byte did not change the digest — the comparison the "
        "lead-stalls OT-008 check relies on is not reading the content"
    )


def test_the_roster_walk_actually_sees_the_files_it_judges() -> None:
    """The guard on the other guard: an empty walk must not read as 'clean'.

    `added` is computed as a set difference, and the empty set is a subset of
    everything — so a walk that returned nothing at all, because the path
    constant was stale or `rglob` was handed the wrong root, would report no
    additions and pass. Pinning the roster to its recorded members closes that:
    the walk has to FIND the three shipped hook files before its silence about
    a fourth means anything.
    """
    roster = _hooks_roster()

    assert roster == PRE_CHANGE_HOOKS_ROSTER, (
        f"the walk over {HOOKS_DIR.relative_to(REPO_ROOT)} sees {sorted(roster)}, "
        f"expected {sorted(PRE_CHANGE_HOOKS_ROSTER)} — the roster recorded at "
        f"base commit {BASE_COMMIT[:7]}"
    )
    assert not any(name.endswith(_IGNORED_SUFFIX) for name in roster), (
        f"the carve-out leaked a {_IGNORED_SUFFIX} file into the roster: "
        f"{sorted(roster)}. That file exists in a working tree and not in a "
        f"detached worktree, so leaving it in makes this module answer "
        f"differently on two checkouts of one commit."
    )


# ===========================================================================
# lead-stalls NFR-001's fourth clause -- THE SOURCE-FILE CAP
# ===========================================================================
#
# Everything above this line is a PROHIBITION, and a prohibition is a standing
# property of the tree: "no new hook event" is answerable by reading
# `hooks.json` today, with no reference to any other commit. The cap is not
# that shape. "The change touches at most eleven source files" is a claim about
# a DIFF, and a diff needs two commits, only one of which a test can ever
# stand on.
#
# THE DIFF IS COMPUTED, AND THE RULING THAT SAID IT COULD NOT BE WAS WRONG
# ------------------------------------------------------------------------
# This block used to argue the opposite, and the argument cost the run a second
# silent overrun, so it is left on the record rather than quietly replaced. It
# ran: `git diff dda6154 HEAD` answers the question today and stops answering it
# the moment this branch lands, because a squash or a rebase drops the base
# commit and, where the commit survives, `HEAD` keeps moving -- so on the
# default branch a month from now the same command reports every file every
# later run touched, and the rung fails forever on work it was never about. A
# merge-base against the default branch degrades from the other side: once this
# work IS the default branch the merge-base is `HEAD`, the diff is empty, and
# the rung passes while measuring nothing. A landmine and a tautology.
#
# Both halves of that are true, and the conclusion drawn from them was still
# wrong, for two reasons.
#
# THE FIRST: what it chose instead was the tautology it was trying to avoid.
# With the diff recorded rather than computed, the count rung compared
# `len(SANCTIONED_SOURCE_FILES)` against `SOURCE_FILE_CAP` -- nine against nine,
# two hand-typed literals in this file, true by construction whatever the run
# did. The one rung that could see past them walked the tree for a CITATION, so
# it saw only files that name this run in their prose. `foundry_spawn.py` was
# changed by this run in dc44462, +81/-3, and cites its work as a bare `D-065`;
# `grep -c "lead-stalls"` over it returns 0. So the run stood at ten source
# files while every rung here reported nine and passed. That is D-070 and
# D-071. A counter keyed on citations cannot enforce a cap on changes.
#
# THE SECOND: the lifetime objection is not an objection to the diff, because
# two shipped rungs above already carry it. `PRE_CHANGE_HOOKS_JSON_SHA256` is
# pinned to this same base commit, and the first future run that legitimately
# edits a hook makes it fail -- on work it was never about, in exactly the shape
# the argument called a landmine. `PRE_CHANGE_HOOKS_ROSTER` is the same. This
# module is one release's containment record, pinned to one commit, and when a
# later run needs the tree to move past it that run re-bases these constants or
# retires them, the digest and the base sha together. The diff is no worse than
# the digest, and the asymmetry was the error.
#
# WHAT IS GUARDED, AND WHAT A MISSING MEASUREMENT DOES
# ----------------------------------------------------
# The narrow half of the argument stands: the measurement is not always
# available. `git` may be absent, the checkout may not be a repository, and a
# shallow clone or a squash-merge drops `BASE_COMMIT` outright.
# `_measured_source_files` returns `None` in each of those cases and the two
# measured rungs SKIP, on a message that names all three so the reader can tell
# which one this checkout is -- the helper does not report which probe refused,
# and the message does not pretend it does. A skip is
# reported by pytest and a pass is not, and the recorded roster's own rungs keep
# running underneath -- so an unmeasurable checkout loses the measurement and
# does not silently gain a green.
#
# The measurement reads the COMMITTED change, `BASE_COMMIT..HEAD`, which is what
# the gate re-executes and what eventually lands. An edit not yet committed is
# outside it by construction, and the citation walk is the rung that sees one:
# it reads the working tree, so it catches a cited file the moment it is
# written. The two are complements, and neither is the other's guard.
#
# THE ROSTER'S JOB, NOW THAT IT IS NOT THE MEASUREMENT
# ----------------------------------------------------
# `SANCTIONED_SOURCE_FILES` is no longer the count. It is the record of WHAT
# COMPELLED each file past the original four, which is the half of
# lead-stalls NFR-001 no diff can supply, and it is asserted EQUAL to the
# measured set -- so a file that appears in the change and not in the record
# fails, and a name kept in the record after its file stopped changing fails
# too. `UNCITED_SANCTIONED_FILES` is narrower than it was: it now carves the
# citation walk only, and the cap no longer depends on it.

#: lead-stalls NFR-001's cap, as the spec declares it AFTER its fourth
#: amendment, of 2026-09-22. It read four until the first, of 2026-09-16.
#:
#: FIRST RULING, 2026-09-16: four to seven. The build overran it -- seven
#: non-exempt source files against a cap of four -- and nothing in the suite
#: noticed, because nothing in the suite counted source files; that silence is
#: the defect this block closes. The user was offered a halt, an amendment and
#: a revert, and chose the amendment, so the number below moved by a ruling and
#: NOT by anything in this file. A test cannot satisfy a violated requirement,
#: and this one does not claim to: it supplies the enforcement the requirement
#: never had.
#:
#: SECOND RULING, 2026-09-19, GRIND cycle 16: seven to NINE. lead-stalls D-057
#: (concern C-008, filed by casting imperatives) is a state no accepted
#: transition left -- a class still ESCALATED with nothing open at a clean
#: FULL F2 -- and its fix is a rung of the transition graph, which none of the
#: seven files holds. The user ruled to fix it in this run and authorised the
#: cap past seven; the two files it compelled are named on the roster below.
#: This ruling landed in the WRONG ORDER: the number here moved in the D-057
#: fix (87a26ae) while the spec still read seven -- the reverse of the order
#: the first rung below demands. D-059 wrote the amendment into
#: lead-stalls NFR-001 afterwards, naming both files and what compelled each,
#: so the spec and this constant now state one number.
#:
#: THIRD RULING, 2026-09-20, GRIND cycle 21: nine to TEN, and this time the spec
#: moved FIRST. lead-stalls D-070 found the change already standing at ten while
#: every rung here reported nine: `tools/foundry_spawn.py` was changed in
#: dc44462 by the D-065 fix, +81/-3, and the citation walk could not see it. Both
#: halves of that fix land in the spawn layer and nowhere else -- the stream
#: dispatch prompt is where a door-less stream's ledger path can be named at
#: all, and `_inspect_stream_dispatches` is the record that stands in for the
#: `spawns.log` entry a bare `Agent(...)` call never writes -- so it could not
#: have landed inside the nine. Unlike the two rulings above, this one rests on
#: D-070's own stated remedy rather than a separate user ruling: the only
#: alternative the defect offers is reverting a shipped fix for a LIVE defect.
#:
#: FOURTH RULING, 2026-09-22, GRIND cycle 24: ten to ELEVEN, and the spec moved
#: first again. The user ruled "Fix C-012 only" and authorised that one fix
#: past the cap; the lead filed it as lead-stalls D-083 (fallout of D-081). Its
#: fix, 441a24a, is a rung of `_done_preconditions` in
#: `tools/orchestration/gates.py`: the one seal both F6 transitions and
#: `Foundry-Gate('done')` share, which read none of the nyquist result files
#: D-081 taught the router to serve an unfiled ESCALATE_IMPL_BUG from, so all
#: three doors sealed F6 over it. None of the ten holds that seal. The list the
#: rung refuses on went to the leaf `artifacts.py`, already on the roster,
#: because the verifier and lifecycle layers may not reach each other at any
#: depth (concern C-014) -- so the ruling moved the cap by exactly one file.
#:
#: The cap is re-typed here because it cannot be read from the spec. Both spec
#: paths are gitignored (`.gitignore:15-16`), so neither exists in the detached
#: worktree the evidence gate re-executes in, and a rung that parses the spec
#: would answer differently on two checkouts of one commit. That is also why
#: the number is checked against the MEASURED change below and not against the
#: roster beside it: two literals in one file agree with each other by
#: construction, which is the whole of D-071.
#:
#: FIFTH RULING, 2026-10-02, the 4.11.2 patch: eleven to TWELVE. guild#20 --
#: the evidence runner handed the MCP stdio transport to every evidence
#: command as its stdin, so an npm child that set its stdin non-blocking set
#: the server's transport non-blocking too, and the server exited after a
#: long sweep. The fix is one argument on the one `Popen` that launches an
#: evidence command, which lives in `tools/worktree_helpers.py` and in none
#: of the eleven. This ruling is the maintainer's to confirm on the PR, not
#: one a lead-stalls spec amendment made first.
SOURCE_FILE_CAP = 12

#: The source files this release is sanctioned to touch, repo-relative and
#: POSIX-spelled. Four were anticipated when lead-stalls NFR-001 was written;
#: eight were compelled afterwards and are the reason the cap moved, five times.
#: Each entry says which it is, because a roster nobody can audit is just a
#: longer number -- and since D-071 the number comes from the measured change,
#: so what this set contributes is exactly the part a diff cannot: WHY.
SANCTIONED_SOURCE_FILES = frozenset(
    {
        # The four the requirement anticipated.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/artifacts.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/guidance.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/teams.py",
        # Compelled by Locked lead-stalls GI-005: every Accept-Casting response
        # carries `next_call`, and the schema refusal is composed in the
        # dispatch layer BEFORE the handler is entered, so no edit to the four
        # above could reach it.
        "plugins/foundry/mcp-server/src/foundry_mcp/server.py",
        # Compelled by the user's explicit in-run instruction, after
        # `Foundry-Tasks` resolved every `owning_casting` to null on this run's
        # string casting ids.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/directives.py",
        # Compelled by the user's request: unpin serena past the
        # `languages` -> `language_servers` rename, and make `doctor` fail on a
        # project that cannot load.
        "plugins/foundry/scripts/serena-daemon.sh",
        # Compelled by lead-stalls D-057 on the user's ruling of 2026-09-19:
        # the F2 re-open of a clean FULL cycle a held class holds DONE shut
        # over is a rung of `_inspect_start_preconditions`, and the held-class
        # union it reads moved to the leaf both layers may import.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/transitions.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/escalation.py",
        # Compelled by lead-stalls D-065, and found by lead-stalls D-070 only
        # after the fact: a stream is spawned by a bare `Agent(...)` call from
        # the `run_streams` list, so it passes through no door and no
        # `spawns.log` record exists for it to supersede a terminal ledger line
        # from an earlier cycle. Both halves of the fix -- the ledger path in
        # the stream dispatch prompt, and `_inspect_stream_dispatches` standing
        # in for the missing record -- live in the spawn layer, which none of
        # the nine above holds.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_spawn.py",
        # Compelled by lead-stalls D-083 on the user's ruling of 2026-09-22,
        # "Fix C-012 only": the unfiled nyquist ESCALATE_IMPL_BUG rung of
        # `_done_preconditions`, the one seal both F6 transitions and
        # `Foundry-Gate('done')` share, which none of the ten above holds. The
        # list it refuses on lives in `artifacts.py`, already on this roster,
        # because the verifier and lifecycle layers may not reach each other.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/gates.py",
        # Compelled by guild#20 in the 4.11.2 patch: `_run_command_with_timeout`
        # is the only launch of an evidence command, and its child must get
        # /dev/null as stdin rather than the server's stdio transport.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/worktree_helpers.py",
    }
)  # 12 items

#: The four sanctioned files that carry no `lead-stalls `-qualified citation,
#: so the citation walk cannot see them. `directives.py` and `foundry_spawn.py`
#: cite their work by the bare run-local defect id, which the convention in
#: `tests/test_spec_id_convention.py` permits and which is not scoped to this
#: run; `serena-daemon.sh` carries no citation at all. Pinned by name so the
#: citation rung can assert EQUALITY rather than one-sided containment -- and
#: left uncorrected here because all three belong to other castings of this run.
#:
#: This set is the measure of the citation walk's blind spot, and it is what
#: lead-stalls D-070 walked out through: `foundry_spawn.py` was a third file of
#: exactly this shape that nobody had added, so it escaped the count instead of
#: being counted and sanctioned. The cap no longer reads this set at all -- the
#: measured rungs below see a changed file whether it cites anything or not --
#: which is why a fourth entry can no longer hide a breach.
UNCITED_SANCTIONED_FILES = frozenset(
    {
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/directives.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/foundry_spawn.py",
        "plugins/foundry/scripts/serena-daemon.sh",
        # The 4.11.2 patch cites its issue, guild#20, not a lead-stalls id.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/worktree_helpers.py",
    }
)  # 4 items

#: What counts as source at all. Anything else in the change -- a manifest, a
#: lockfile, a README, an evidence log -- is outside the cap because the cap is
#: about code.
_CAPPED_SOURCE_SUFFIXES = frozenset({".py", ".sh"})

#: lead-stalls GI-006 / FR-012: the cap governs source only, and the build may
#: add or extend test files freely. Any path with a `tests` component is a test.
_TESTS_PART = "tests"

#: lead-stalls NFR-001's other exemption, the release-version manifests. Four of
#: the six are already outside `_CAPPED_SOURCE_SUFFIXES` and would be exempt
#: without this set; it names all six anyway, so the rule is stated where a
#: reader looks for it, and so a future release site that IS a `.py` is exempt
#: without anyone inventing a new rule for it.
_RELEASE_VERSION_MANIFESTS = frozenset(
    {
        "plugins/foundry/.claude-plugin/plugin.json",
        ".claude-plugin/marketplace.json",
        "plugins/foundry/mcp-server/src/foundry_mcp/__init__.py",
        "plugins/foundry/mcp-server/pyproject.toml",
        "plugins/foundry/mcp-server/uv.lock",
        "plugins/foundry/mcp-server/tests/test_release_version.py",
    }
)  # 6 items

#: The roots the citation walk covers: every place foundry plugin source lives.
#: Scoped to these three rather than to the repo root because the repo root
#: carries untracked directories -- `foundry-archive/`, `.serena/`, a plugin
#: `.venv` -- that exist in a working tree and not in a `git worktree add
#: --detach` checkout, and a walk that read them would answer differently on
#: two checkouts of one commit. The three below are tracked source directories
#: with no untracked `.py` or `.sh` in either checkout.
_SOURCE_WALK_ROOTS = (
    "plugins/foundry/mcp-server/src",
    "plugins/foundry/scripts",
    "plugins/foundry/hooks",
)

#: The qualifier that marks a construct as serving THIS run. This is a data
#: literal handed to a search, not a citation, so the convention in
#: `tests/test_spec_id_convention.py` does not scan it -- which is also why the
#: qualifier is spelled once, here, instead of inline at the call site.
_RUN_CITATION = "lead-stalls "


def _cap_class(rel: str) -> str:
    """Which side of lead-stalls NFR-001's cap a repo-relative path falls on.

    Returns one of `not-source`, `test`, `release-manifest`, `source`. Only
    `source` counts against the cap; the other three are the exemptions, spelled
    as an executable rule so the next run does not hand-roll them again. The
    ordering is deliberate: `test_release_version.py` is both a test and a
    release manifest, and it is reported as a test because lead-stalls GI-006
    is the broader exemption and the one a reader will be looking for.
    """
    path = PurePosixPath(rel)
    if path.suffix not in _CAPPED_SOURCE_SUFFIXES:
        return "not-source"
    if _TESTS_PART in path.parts:
        return "test"
    if rel in _RELEASE_VERSION_MANIFESTS:
        return "release-manifest"
    return "source"


def _cited_source_files(
    base: Path = REPO_ROOT,
    roots: tuple[str, ...] = _SOURCE_WALK_ROOTS,
) -> frozenset[str]:
    """Every capped source file under `roots` whose text cites this run.

    Read as text and not as bytes, on purpose: this asks whether a CITATION is
    present, which is a question about characters, unlike the byte-identity
    claim the digest above answers. `base` and `roots` are parameters so the
    walk can be driven over a planted tree and shown to bite, without this
    module ever mutating the one it is judging.
    """
    found: set[str] = set()
    for root in roots:
        root_path = base / root
        if not root_path.is_dir():
            continue
        for path in root_path.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if _cap_class(rel) != "source":
                continue
            if _RUN_CITATION in path.read_text(encoding="utf-8", errors="replace"):
                found.add(rel)
    return frozenset(found)


#: What the two measured rungs report when the change cannot be read in this
#: checkout, spelled once so both rungs skip with the same sentence.
_UNMEASURABLE = (
    "lead-stalls NFR-001's cap is a claim about the change from "
    f"{BASE_COMMIT[:7]}, and this checkout cannot produce it: git is absent, "
    "this is not a repository, or that commit is not an ancestor of HEAD here "
    "-- a shallow clone and a squash-merge both drop it. The measurement is "
    "SKIPPED rather than passed, because a cap rung that reports green over a "
    "change it never read is the whole of lead-stalls D-071. The recorded "
    "roster's own rungs still ran."
)


def _measured_source_files(
    repo: Path = REPO_ROOT,
    base: str = BASE_COMMIT,
) -> frozenset[str] | None:
    """The capped source files `base..HEAD` actually changed, or `None`.

    This is lead-stalls NFR-001's count, taken from the change instead of from
    a literal. `--name-only -z` so a path with a space or a non-ASCII byte in
    it arrives whole rather than quoted, and every path is put through
    `_cap_class`, so the two exemptions are applied by the same rule the
    recorded rungs use and cannot drift from it.

    `None` means the measurement is UNAVAILABLE, and it is a distinct answer
    from the empty set: an empty set says the change touched no source file,
    and `None` says nobody here can tell. Every git call is checked rather
    than trusted -- a `git` that is missing, a directory that is not a
    repository, and a `BASE_COMMIT` that this checkout does not carry all
    return `None`, and the rungs that consume it skip.

    `repo` and `base` are parameters so the measurement can be driven over a
    planted repository whose answer is known in advance, without this module
    ever committing anything to the tree it judges.
    """

    def _git(*args: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                ["git", "-C", str(repo), *args],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            # `git` not on PATH, or `repo` gone. Unavailable, never green.
            return None

    for probe in (
        ("rev-parse", "--verify", "--quiet", "HEAD"),
        ("cat-file", "-e", f"{base}^{{commit}}"),
        ("merge-base", "--is-ancestor", base, "HEAD"),
    ):
        done = _git(*probe)
        if done is None or done.returncode != 0:
            return None

    changed = _git("diff", "--name-only", "-z", f"{base}..HEAD")
    if changed is None or changed.returncode != 0:
        return None

    return frozenset(
        rel
        for rel in changed.stdout.split("\0")
        if rel and _cap_class(rel) == "source"
    )


def test_the_measured_change_fits_the_declared_cap() -> None:
    """lead-stalls NFR-001's cap, measured from the change it is a claim about.

    This is the rung the cap did not have.
    `test_the_sanctioned_source_roster_fits_the_declared_cap` compares the
    recorded roster against the same number, and that comparison is two
    literals in this file agreeing with each other; this one reads
    `BASE_COMMIT..HEAD` and counts what the release actually touched, so a file
    changed with no citation, no roster entry and no announcement is counted
    all the same. lead-stalls D-070 is exactly that file.
    """
    measured = _measured_source_files()
    if measured is None:
        pytest.skip(_UNMEASURABLE)

    assert len(measured) <= SOURCE_FILE_CAP, (
        f"this release changed {len(measured)} non-exempt source files between "
        f"{BASE_COMMIT[:7]} and HEAD, against lead-stalls NFR-001's cap of "
        f"{SOURCE_FILE_CAP}: {sorted(measured)}. The cap is a ceiling on how "
        f"far a payload-and-prose fix may spread. Move the work back inside "
        f"it, or get the cap amended in the spec first and record on the "
        f"roster which file was compelled and by what -- in that order, "
        f"because a cap edited to match the code it failed to bound is not a "
        f"cap."
    )


def test_the_sanctioned_roster_is_exactly_the_measured_change() -> None:
    """The roster earns its place against the change, in both directions.

    lead-stalls NFR-001 needs two things a diff cannot supply on its own: the
    count, and WHY each file past the original four was compelled. The count is
    measured above; the why is the roster's comments. This rung is what keeps
    the second honest about the first.

    Equality, for the same reason `PRE_CHANGE_HOOKS_ROSTER` is asserted as
    equality. An unrecorded path is a file that spread without a reason on the
    record -- lead-stalls D-070's shape, which the citation walk could not see.
    A recorded path that no longer appears in the change is a roster that has
    outlived its diff, and it inflates the documented count over the real one.
    """
    measured = _measured_source_files()
    if measured is None:
        pytest.skip(_UNMEASURABLE)

    unrecorded = sorted(measured - SANCTIONED_SOURCE_FILES)
    assert not unrecorded, (
        f"{len(unrecorded)} source file(s) changed between {BASE_COMMIT[:7]} "
        f"and HEAD and are not on the sanctioned roster: {unrecorded}. That is "
        f"{len(measured)} source files against lead-stalls NFR-001's cap of "
        f"{SOURCE_FILE_CAP}. Either the work belongs in a file already on the "
        f"roster and should move there, or the cap needs amending in the spec "
        f"and the roster needs the new path with a note saying what compelled "
        f"it. Being uncited is not being exempt: this rung reads the change, "
        f"not the prose."
    )

    stale = sorted(SANCTIONED_SOURCE_FILES - measured)
    assert not stale, (
        f"{len(stale)} roster entr(y/ies) are not in the change from "
        f"{BASE_COMMIT[:7]} to HEAD: {stale}. The roster records which source "
        f"files this release was allowed to touch and why; a name whose file "
        f"this release never changed counts against the cap on paper and not "
        f"in the tree. Drop it, or -- if the edit was reverted on purpose -- "
        f"drop it and say so in the amendment note that admitted it."
    )


def test_the_measurement_actually_reads_the_change(tmp_path: Path) -> None:
    """The guard on the measurement: drive it over a repository it must get right.

    Every rung above passes when the measured set matches the roster, which is
    also what a measurement that returned the roster for the wrong reason would
    do -- a `git diff` against the working tree rather than the base, or a
    filter that let the exemptions through. So the measurement is driven here
    over a planted repository whose answer is settled in advance: two changed
    source files it must find, one source file present at the base and never
    touched that it must NOT find (the case no citation walk can decide), and
    the three exemption shapes it must drop. Planted under `tmp_path`, and
    committed there, so the tree this module judges is never written to.

    Both unavailable answers are driven too -- a base commit the checkout does
    not carry, and a path git cannot enter -- because `None` is what makes the
    skip honest: if either came back as the empty set instead, the cap rung
    would report a change of zero source files and pass.
    """
    src = tmp_path / "plugins" / "foundry" / "mcp-server" / "src" / "foundry_mcp"
    scripts = tmp_path / "plugins" / "foundry" / "scripts"
    tests = tmp_path / "plugins" / "foundry" / "mcp-server" / "tests"
    for directory in (src, scripts, tests):
        directory.mkdir(parents=True)

    def _plant(path: Path, body: str) -> None:
        path.write_text(body, encoding="utf-8")

    # The base commit: everything exists, nothing has been changed yet.
    _plant(src / "changed.py", "x = 1\n")
    _plant(src / "untouched.py", "y = 1\n")
    _plant(src / "__init__.py", '__version__ = "1.10.0"\n')
    _plant(tests / "test_planted.py", "def test_x() -> None:\n    pass\n")
    _plant(tmp_path / "README.md", "before\n")
    _plant(scripts / "planted.sh", "#!/bin/sh\necho before\n")

    def _git(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            capture_output=True,
            text=True,
            check=True,
        )

    _git("init", "-q")
    _git("config", "user.email", "t@t")
    _git("config", "user.name", "t")
    _git("add", "-A")
    _git("commit", "-qm", "base")
    base = _git("rev-parse", "HEAD").stdout.strip()

    # The change: one source file edited, one added, and one of each exemption
    # shape touched. `untouched.py` is deliberately left alone.
    _plant(src / "changed.py", "x = 2\n")
    _plant(scripts / "planted.sh", "#!/bin/sh\necho after\n")
    _plant(src / "added.py", "z = 1\n")
    _plant(src / "__init__.py", '__version__ = "1.10.1"\n')
    _plant(tests / "test_planted.py", "def test_x() -> None:\n    assert True\n")
    _plant(tmp_path / "README.md", "after\n")
    _git("add", "-A")
    _git("commit", "-qm", "change")

    measured = _measured_source_files(repo=tmp_path, base=base)

    assert measured == {
        "plugins/foundry/mcp-server/src/foundry_mcp/changed.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/added.py",
        "plugins/foundry/scripts/planted.sh",
    }, (
        f"the measurement returned {sorted(measured or [])}. It has to find "
        f"the edited source file, the added one and the changed `.sh` -- that "
        f"is how a file past the roster is counted at all -- and it has to "
        f"leave out the source file this change never touched, the test file "
        f"lead-stalls GI-006 exempts, the `.py` release manifest, and the "
        f"non-source file."
    )

    absent_base = _measured_source_files(repo=tmp_path, base="0" * 40)
    assert absent_base is None, (
        f"a base commit this checkout does not carry measured "
        f"{sorted(absent_base or [])} instead of reporting itself "
        f"unavailable. That is the shallow-clone and squash-merge case, and "
        f"returning a set there turns a missing measurement into a green cap."
    )

    no_checkout = _measured_source_files(repo=tmp_path / "not-a-checkout", base=base)
    assert no_checkout is None, (
        f"a path git cannot enter measured {sorted(no_checkout or [])} instead "
        f"of reporting itself unavailable. Every git call here is checked for "
        f"its exit status rather than trusted for its stdout, and an empty "
        f"stdout from a failed call reads as a change of nothing at all."
    )


def test_the_sanctioned_source_roster_fits_the_declared_cap() -> None:
    """The recorded roster's own arithmetic, which is NOT the cap's enforcement.

    On its own this compares two literals in this file, and lead-stalls D-071
    is the record of what happens when that is mistaken for enforcement: nine
    against nine is true by construction, and stayed true while the run stood
    at ten. It is kept, narrowed to the job it can do, because it is the one
    cap rung that runs in a checkout where the change cannot be measured -- the
    two rungs above skip there, and this holds the documented count to the
    documented cap so the recording at least contradicts itself out loud. The
    enforcement is `test_the_measured_change_fits_the_declared_cap`.
    """
    count = len(SANCTIONED_SOURCE_FILES)

    assert count <= SOURCE_FILE_CAP, (
        f"this release touches {count} non-exempt source files against "
        f"lead-stalls NFR-001's cap of {SOURCE_FILE_CAP}: "
        f"{sorted(SANCTIONED_SOURCE_FILES)}. The cap is a ceiling on how far a "
        f"payload-and-prose fix may spread. Move the work back inside it, or "
        f"get the cap amended in the spec first and record here which file was "
        f"compelled and by what -- in that order, because a cap edited to match "
        f"the code it failed to bound is not a cap."
    )


def test_every_sanctioned_source_file_is_present_and_counts_against_the_cap() -> None:
    """The roster's members are paths in this tree, not strings in this file.

    A roster of eleven names that resolve to nothing would satisfy the cap
    arithmetic above perfectly. Each entry has to exist, and has to classify as
    capped source under the exemption rule -- an entry that is really a test or
    a manifest is padding the count with something lead-stalls NFR-001 exempts.
    """
    missing = sorted(
        rel for rel in SANCTIONED_SOURCE_FILES if not (REPO_ROOT / rel).is_file()
    )
    assert not missing, (
        f"{len(missing)} sanctioned path(s) are not files in this tree: "
        f"{missing}. The roster records which source files this release was "
        f"allowed to touch; a name that resolves to nothing records nothing."
    )

    misclassified = sorted(
        (rel, _cap_class(rel))
        for rel in SANCTIONED_SOURCE_FILES
        if _cap_class(rel) != "source"
    )
    assert not misclassified, (
        f"{len(misclassified)} roster entr(y/ies) are exempt from lead-stalls "
        f"NFR-001's cap and so cannot count against it: {misclassified}. "
        f"lead-stalls GI-006 and lead-stalls FR-012 exempt test files, and "
        f"lead-stalls NFR-001 exempts the release-version manifests. An exempt "
        f"path on this roster inflates the count with something the "
        f"requirement does not count."
    )

    uncited_strays = sorted(UNCITED_SANCTIONED_FILES - SANCTIONED_SOURCE_FILES)
    assert not uncited_strays, (
        f"{uncited_strays} are carved out of the citation walk but are not on "
        f"the sanctioned roster. The carve-out exists to let the citation rung "
        f"assert equality; carving out a path that is not sanctioned widens "
        f"that rung's blind spot instead of explaining it."
    )


def test_no_unsanctioned_source_file_cites_this_run() -> None:
    """The rung that sees a file before it is committed.

    The measured rungs above read `BASE_COMMIT..HEAD`, so a file edited and not
    yet committed is outside them. This one reads the WORKING TREE: every
    capped source file under the walked roots that cites this run has to be on
    the roster, whether it has been committed, staged, or only written. That is
    its job now -- it is no longer the cap's only eyes, which is what
    lead-stalls D-070 exposed when a changed file that cites nothing walked
    straight past it.

    Equality rather than containment, and for the same reason
    `PRE_CHANGE_HOOKS_ROSTER` is asserted as equality -- a sanctioned file that
    LOST its citation is the evidence base moving, and equality reports it for
    free with a message that names which direction went.

    What this cannot see is stated rather than papered over: a source file
    edited with no citation at all is invisible here, and three of the eleven are
    exactly that shape. The measured rungs are what cover it.
    """
    cited = _cited_source_files()
    expected = SANCTIONED_SOURCE_FILES - UNCITED_SANCTIONED_FILES

    unsanctioned = sorted(cited - SANCTIONED_SOURCE_FILES)
    assert not unsanctioned, (
        f"{len(unsanctioned)} source file(s) cite this run and are not on the "
        f"sanctioned roster: {unsanctioned}. That is at least "
        f"{len(SANCTIONED_SOURCE_FILES) + len(unsanctioned)} source files "
        f"against lead-stalls NFR-001's cap of {SOURCE_FILE_CAP}. Either the "
        f"work belongs in a file already on the roster and should move there, "
        f"or the cap needs amending in the spec and the roster needs the new "
        f"path with a note saying what compelled it."
    )

    lost_citation = sorted(expected - cited)
    assert not lost_citation, (
        f"{len(lost_citation)} sanctioned file(s) no longer cite this run: "
        f"{lost_citation}. The walk is how this module sees an uncommitted "
        f"twelfth file, and it can only see one while the citation convention "
        f"holds over the files it already knows about. Restore the citation, "
        f"or move the path into the carve-out and say in its comment why it "
        f"lost one."
    )


def test_the_exemption_rule_actually_exempts_and_actually_counts() -> None:
    """The guard on the exemption rule: prove it sorts known paths correctly.

    `_cap_class` is what turns the changed paths into a count of eleven, so a
    version of it that returned `source` for everything would report a wild
    overrun, and one that returned an exemption for everything would report
    zero and pass. Both directions are driven here on paths whose answer is
    settled by lead-stalls NFR-001 and lead-stalls GI-006 themselves.
    """
    exempt = {
        # lead-stalls GI-006 / lead-stalls FR-012 -- tests are free.
        "plugins/foundry/mcp-server/tests/test_containment.py": "test",
        "plugins/foundry/mcp-server/tests/orchestration/test_teams.py": "test",
        # Both a test and a release site; reported as the broader exemption.
        "plugins/foundry/mcp-server/tests/test_release_version.py": "test",
        # The release-version manifest that is a `.py` and so needs the set.
        "plugins/foundry/mcp-server/src/foundry_mcp/__init__.py": "release-manifest",
        # The release sites the suffix rule already handles.
        "plugins/foundry/.claude-plugin/plugin.json": "not-source",
        ".claude-plugin/marketplace.json": "not-source",
        "plugins/foundry/mcp-server/pyproject.toml": "not-source",
        "plugins/foundry/mcp-server/uv.lock": "not-source",
        # Neither source nor a release site, and still outside the cap.
        "README.md": "not-source",
        "evidence/casting-release-full-suite.log": "not-source",
        "plugins/foundry/hooks/hooks.json": "not-source",
    }
    for rel, expected in exempt.items():
        assert _cap_class(rel) == expected, (
            f"{rel} classifies as {_cap_class(rel)}, expected {expected}. "
            f"lead-stalls NFR-001 exempts test files and release-version "
            f"manifests from its cap; a rule that counts one of them reports "
            f"an overrun that the requirement does not actually forbid."
        )

    for rel in sorted(SANCTIONED_SOURCE_FILES):
        assert _cap_class(rel) == "source", (
            f"{rel} classifies as {_cap_class(rel)} rather than source. Every "
            f"path lead-stalls NFR-001 counts has to come back as source, or "
            f"the cap is measured against a smaller set than the requirement "
            f"names and an overrun reads as compliance."
        )

    # A `.sh` under the plugin is source too -- `serena-daemon.sh` is on the
    # roster because of this limb, and the lead's own count of the overrun was
    # over `*.py` AND `*.sh`.
    assert _cap_class("plugins/foundry/hooks/session-start-serena.sh") == "source", (
        "a shell script under the plugin does not classify as source, so a "
        "release that spread into one would not be counted against "
        "lead-stalls NFR-001's cap at all"
    )


def test_the_citation_walk_actually_catches_an_unsanctioned_file(
    tmp_path: Path,
) -> None:
    """The guard on the walk: plant a file past the roster and prove it is found.

    The citation rung passes when the walk returns exactly the roster, which is
    also what a walk handed a stale root, or one whose read silently failed,
    would do on the `unsanctioned` limb. So the walk is driven here over a
    planted tree where the answer is known in advance: one cited source file
    that must be found, one uncited source file that must not be, one cited
    TEST file that lead-stalls GI-006 exempts and that must not be, and one
    cited non-source file that must not be. Planted under `tmp_path` so the
    tree this module judges is never touched.

    Note which file the walk misses here and the measurement does not:
    `untouched.py` is left out for want of a citation, not for want of a
    change. Read the other way round, a file that WAS changed and cites nothing
    is missed the same way -- lead-stalls D-070 -- which is why this is a guard
    on one of two rungs and not on the cap itself.
    """
    root = tmp_path / "plugins" / "foundry" / "mcp-server" / "src"
    tests = tmp_path / "plugins" / "foundry" / "mcp-server" / "tests"
    root.mkdir(parents=True)
    tests.mkdir(parents=True)

    cited = "# lead-stalls NFR-001: planted.\n"
    (root / "unsanctioned_file.py").write_text(cited, encoding="utf-8")
    (root / "unsanctioned_script.sh").write_text(f"#!/bin/sh\n{cited}", encoding="utf-8")
    (root / "untouched.py").write_text("# unrelated.\n", encoding="utf-8")
    (root / "notes.md").write_text(cited, encoding="utf-8")
    (tests / "test_planted.py").write_text(cited, encoding="utf-8")

    walked = _cited_source_files(
        base=tmp_path,
        roots=(
            "plugins/foundry/mcp-server/src",
            "plugins/foundry/mcp-server/tests",
        ),
    )

    assert walked == {
        "plugins/foundry/mcp-server/src/unsanctioned_file.py",
        "plugins/foundry/mcp-server/src/unsanctioned_script.sh",
    }, (
        f"the walk returned {sorted(walked)}. It has to find both planted "
        f"source files -- that is how an uncommitted file past the roster is "
        f"seen at all -- and it has to leave out the uncited file, the "
        f"non-source file, and the test file lead-stalls GI-006 exempts."
    )
