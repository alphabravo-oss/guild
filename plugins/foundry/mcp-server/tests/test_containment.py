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
diff by hand. The cap rungs, and the long note on why the diff is RECORDED here
rather than computed from git — the same ruling the digest above rests on, for
the same reasons — are at the bottom of this file. That note also says plainly
what those rungs cannot see, because a diff is the one thing no durable test is
able to hold.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

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
# that shape. "The change touches at most seven source files" is a claim about
# a DIFF, and a diff needs two commits, only one of which a test can ever
# stand on.
#
# WHY THE DIFF IS RECORDED HERE RATHER THAN COMPUTED
# --------------------------------------------------
# `git diff dda6154 HEAD` answers the question exactly -- today. It stops
# answering it the moment this branch lands. A squash or a rebase drops the
# base commit; and even where the commit survives, `HEAD` keeps moving, so on
# the default branch a month from now the same command reports every file every
# later run touched, and this rung fails forever on work it was never about. A
# merge-base against the default branch survives the rebase and then degrades
# from the other side: once this work IS the default branch, the merge-base is
# `HEAD`, the diff is empty, and the rung passes while measuring nothing.
# Neither one is a test -- the first is a landmine, the second a tautology.
#
# The module docstring already ruled on exactly this, in the same words and
# against the same commit: a live `git show dda6154:...` reads the right bytes
# and fails in a shallow clone. The digest above is a literal for that reason,
# and the roster beside it is a literal for that reason. A `git diff dda6154`
# down here would contradict a ruling two shipped rungs already rest on. So the
# diff is recorded the same way they are.
#
# WHAT THIS CAN SEE, AND WHAT IT CANNOT, PLAINLY
# ----------------------------------------------
# A recorded roster cannot by itself notice an eighth file: an edited file and
# an untouched file are identical on disk. What DOES leave a mark is this
# codebase's citation convention. `tests/test_spec_id_convention.py` enforces
# it over every prose surface under `tests/`, and every non-obvious construct
# in `src/` carries the same qualified id naming the requirement it serves. So
# an eighth file is caught by its CITATION rather than by its mtime: a source
# file in the walked roots that cites this run and is not on the roster fails
# the rung below, whatever commit it arrived in.
#
# The gap is named rather than papered over. Two of the seven sanctioned files
# carry no qualified citation of this run, so they are pinned by name in
# `UNCITED_SANCTIONED_FILES`, and they are themselves the proof that an eighth
# file could be edited in the same silent way and escape this walk. Nothing in
# this module can see that file. Only a diff can, and a diff is the one thing
# no durable test is able to hold.

#: lead-stalls NFR-001's cap, as the spec declares it AFTER the amendment of
#: 2026-09-16. It read four until this cycle. The build overran it -- seven
#: non-exempt source files against a cap of four -- and nothing in the suite
#: noticed, because nothing in the suite counted source files; that silence is
#: the defect this block closes. The user was offered a halt, an amendment and
#: a revert, and chose the amendment, so the number below moved by a ruling and
#: NOT by anything in this file. A test cannot satisfy a violated requirement,
#: and this one does not claim to: it supplies the enforcement the requirement
#: never had.
#:
#: The cap is re-typed here because it cannot be read from the spec. Both spec
#: paths are gitignored (`.gitignore:15-16`), so neither exists in the detached
#: worktree the evidence gate re-executes in, and a rung that parses the spec
#: would answer differently on two checkouts of one commit.
SOURCE_FILE_CAP = 7

#: The source files this release is sanctioned to touch, repo-relative and
#: POSIX-spelled. Four were anticipated when lead-stalls NFR-001 was written;
#: three were compelled afterwards and are the reason the cap moved. Each entry
#: says which it is, because a roster nobody can audit is just a longer number.
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
    }
)  # 7 items

#: The two sanctioned files that carry no `lead-stalls `-qualified citation, so
#: the citation walk cannot see them. `directives.py` cites its work by the
#: bare run-local defect id, which the convention in
#: `tests/test_spec_id_convention.py` permits and which is not scoped to this
#: run; `serena-daemon.sh` carries no citation at all. Pinned by name so the
#: rung below can assert EQUALITY rather than one-sided containment -- and left
#: uncorrected here because both files belong to other castings of this run.
UNCITED_SANCTIONED_FILES = frozenset(
    {
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/directives.py",
        "plugins/foundry/scripts/serena-daemon.sh",
    }
)  # 2 items

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


def test_the_sanctioned_source_roster_fits_the_declared_cap() -> None:
    """lead-stalls NFR-001's cap, counted.

    On its own this compares two literals, and it is written down as one rung
    rather than dressed up as more: what stops it being a tautology is the
    company it keeps. The rung below makes every member of the roster earn its
    place against the tree, and the rung after that searches the tree for a
    member the roster does not have. This one is the arithmetic those two make
    meaningful.
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

    A roster of seven names that resolve to nothing would satisfy the cap
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
        f"the sanctioned roster. The carve-out exists to let the rung below "
        f"assert equality; carving out a path that is not sanctioned widens "
        f"that rung's blind spot instead of explaining it."
    )


def test_no_unsanctioned_source_file_cites_this_run() -> None:
    """The rung that can actually see an eighth file.

    lead-stalls NFR-001 bounds a DIFF, and the roster above is that diff
    recorded. This is the half that is not recorded: the tree is walked, and
    every capped source file that cites this run has to be on the roster.
    Equality rather than containment, and for the same reason
    `PRE_CHANGE_HOOKS_ROSTER` is asserted as equality -- a sanctioned file that
    LOST its citation is the evidence base moving, and equality reports it for
    free with a message that names which direction went.

    Read the block above these declarations for what this cannot see: a source
    file edited with no citation at all is invisible here, and two of the seven
    are exactly that shape.
    """
    cited = _cited_source_files()
    expected = SANCTIONED_SOURCE_FILES - UNCITED_SANCTIONED_FILES

    unsanctioned = sorted(cited - SANCTIONED_SOURCE_FILES)
    assert not unsanctioned, (
        f"{len(unsanctioned)} source file(s) cite this run and are not on the "
        f"sanctioned roster: {unsanctioned}. That is "
        f"{len(SANCTIONED_SOURCE_FILES) + len(unsanctioned)} source files "
        f"against lead-stalls NFR-001's cap of {SOURCE_FILE_CAP}. Either the "
        f"work belongs in one of the seven and should move there, or the cap "
        f"needs amending in the spec and the roster needs the new path with a "
        f"note saying what compelled it."
    )

    lost_citation = sorted(expected - cited)
    assert not lost_citation, (
        f"{len(lost_citation)} sanctioned file(s) no longer cite this run: "
        f"{lost_citation}. The walk is how this module detects an eighth file, "
        f"and it can only detect one while the citation convention holds over "
        f"the files it already knows about. Restore the citation, or move the "
        f"path into the carve-out and say in its comment why it lost one."
    )


def test_the_exemption_rule_actually_exempts_and_actually_counts() -> None:
    """The guard on the exemption rule: prove it sorts known paths correctly.

    `_cap_class` is what turns 36 changed paths into a count of seven, so a
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


def test_the_citation_walk_actually_catches_an_eighth_file(tmp_path: Path) -> None:
    """The guard on the walk: plant an eighth file and prove it is found.

    The rung above passes when the walk returns exactly the roster, which is
    also what a walk handed a stale root, or one whose read silently failed,
    would do on the `unsanctioned` limb. So the walk is driven here over a
    planted tree where the answer is known in advance: one cited source file
    that must be found, one uncited source file that must not be, one cited
    TEST file that lead-stalls GI-006 exempts and that must not be, and one
    cited non-source file that must not be. Planted under `tmp_path` so the
    tree this module judges is never touched.
    """
    root = tmp_path / "plugins" / "foundry" / "mcp-server" / "src"
    tests = tmp_path / "plugins" / "foundry" / "mcp-server" / "tests"
    root.mkdir(parents=True)
    tests.mkdir(parents=True)

    cited = "# lead-stalls NFR-001: planted.\n"
    (root / "eighth_file.py").write_text(cited, encoding="utf-8")
    (root / "eighth_script.sh").write_text(f"#!/bin/sh\n{cited}", encoding="utf-8")
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
        "plugins/foundry/mcp-server/src/eighth_file.py",
        "plugins/foundry/mcp-server/src/eighth_script.sh",
    }, (
        f"the walk returned {sorted(walked)}. It has to find both planted "
        f"source files -- that is the whole of how an eighth file is detected "
        f"-- and it has to leave out the uncited file, the non-source file, "
        f"and the test file lead-stalls GI-006 exempts."
    )
