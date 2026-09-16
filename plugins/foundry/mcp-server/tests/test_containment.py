"""lead-stalls OT-008 / OT-009 / GI-002 / FR-009 — the hook surface did not move.

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
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

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
