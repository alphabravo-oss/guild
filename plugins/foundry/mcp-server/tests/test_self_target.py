"""Casting 2 — a self-targeting run executes on its own build (US-006).

One regression test per acceptance criterion, each docstring quoting the
requirement it proves. Built on the synthetic-run shape
``tests/test_escalation.py`` establishes, with two additions this preflight
needs: a fake foundry plugin manifest under ``tmp_path``, and a monkeypatched
commit lookup so the test decides what git would have said.

  AC-025 / FR-018   self-target is EXACTLY the presence of a plugin.json named
                    foundry under project_root; the matched manifest's version
                    and the tree's HEAD are what get compared.
  AC-026 / ST-009   on mismatch the refusal names the reason and prints the
                    exact `claude --plugin-dir` command; with no foundry
                    manifest nothing is compared and no warning is emitted.
  CT-010            the state.json fields, on every run.
  FR-035            a commit that could not be read is reported as unknown and
                    is NEVER treated as a match.
  OT-017            the end-to-end refusal, message included.

WHY THE COMMIT LOOKUP IS MONKEYPATCHED AND THE MANIFESTS ARE REAL FILES: the
preflight's whole subject is a DISAGREEMENT between two trees, and only one of
them (the executing server's) exists on this machine. Faking the manifests
makes the target tree constructible; faking the commit lookup makes the
disagreement expressible without building two git repositories to make them
differ. What is NOT faked is the comparison itself, which is the thing under
test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry as F
from foundry_mcp.tools.foundry_state import ARCHIVE_DIR, clear_active_run


SERVER_COMMIT = "1" * 40
TREE_COMMIT = "2" * 40
SERVER_ROOT = Path("/fake/cache/guild/foundry/4.9.0")


@pytest.fixture(autouse=True)
def _isolate_active_run():
    """foundry_init sets a module-level active run; never leak it."""
    clear_active_run()
    yield
    clear_active_run()


@pytest.fixture
def server(monkeypatch, tmp_path):
    """Pin the EXECUTING server's identity: root, version, plugin version, commit.

    Returns a mutable dict the test edits to express the disagreement it wants.
    ``_git_head`` is keyed on the path it is asked about, so the working tree's
    HEAD and the server's are independently steerable — the two values the
    preflight compares.
    """
    identity = {
        "server_root": SERVER_ROOT,
        "server_version": "1.8.0",
        "plugin_version": "4.9.0",
        "commits": {str(SERVER_ROOT): SERVER_COMMIT, str(tmp_path): TREE_COMMIT},
    }

    monkeypatch.setattr(F, "_executing_server_root", lambda: identity["server_root"])
    monkeypatch.setattr(
        F, "_executing_server_version", lambda: identity["server_version"]
    )

    def _plugin_version(plugin_dir: Path) -> str:
        # The executing server's manifest is not on disk; every OTHER plugin
        # dir is a real file under tmp_path and is read for real.
        if Path(plugin_dir) == identity["server_root"]:
            return identity["plugin_version"]
        return _real_plugin_version(plugin_dir)

    _real_plugin_version = F._plugin_manifest_version
    monkeypatch.setattr(F, "_plugin_manifest_version", _plugin_version)
    monkeypatch.setattr(
        F, "_git_head", lambda root: identity["commits"].get(str(root), F.UNKNOWN_COMMIT)
    )
    return identity


def _write_plugin_manifest(root: Path, *, name: str, version: str, nested: bool = True) -> Path:
    """Write a plugin manifest under ``root``; return the plugin directory."""
    plugin_dir = (root / "plugins" / name) if nested else root
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}), encoding="utf-8"
    )
    return plugin_dir


def _state(result: dict) -> dict:
    return json.loads(
        (Path(result["foundry_dir"]) / "state.json").read_text(encoding="utf-8")
    )


def _runs(tmp_path: Path) -> list[str]:
    archive = tmp_path / ARCHIVE_DIR
    return sorted(p.name for p in archive.iterdir()) if archive.exists() else []


# --- AC-026 / FR-050 / CT-010: the no-manifest branch ------------------------
def test_a_run_with_no_foundry_manifest_compares_nothing(server, tmp_path):
    """AC-026 verbatim: 'for a run with no foundry plugin.json under
    project_root, Foundry-Init records and displays the executing version and
    compares nothing.'

    The commits DISAGREE here on purpose: the ordinary case is a run whose
    target is some other repository entirely, and that run must not be blocked
    by a comparison that means nothing to it."""
    result = F.foundry_init(project_root=str(tmp_path))

    assert "error" not in result, result
    assert result["self_target"] is False
    state = _state(result)
    assert state["self_target"] is False
    assert state["server_commit"] == SERVER_COMMIT
    # No warning key, no comparison result, nothing about a mismatch.
    assert "launch_command" not in result
    assert "mismatch" not in result


def test_a_manifest_named_something_else_is_not_self_target(server, tmp_path):
    """FR-018 / AC-025 — self-target is exactly a plugin.json NAMED FOUNDRY.
    A project containing other plugins is an ordinary target."""
    _write_plugin_manifest(tmp_path, name="forge", version="0.0.1")
    _write_plugin_manifest(tmp_path, name="crew", version="0.0.2")

    result = F.foundry_init(project_root=str(tmp_path))

    assert "error" not in result, result
    assert result["self_target"] is False


# --- AC-027 / FR-017 / OT-018: the recorded fields ---------------------------
def test_state_json_records_every_version_field(server, tmp_path):
    """OT-018 verbatim: 'After a successful init, state.json contains
    server_version, plugin_version, server_root and server_commit'. AC-027 adds
    that Foundry-Next displays them, which reads this same record."""
    result = F.foundry_init(project_root=str(tmp_path))
    state = _state(result)

    assert state["server_version"] == "1.8.0"
    assert state["plugin_version"] == "4.9.0"
    assert state["server_root"] == str(SERVER_ROOT)
    assert state["server_commit"] == SERVER_COMMIT
    assert state["self_target"] is False

    # Echoed on the return too, so a renderer reads what the run recorded
    # rather than re-deriving it.
    for key in ("server_version", "plugin_version", "server_root", "server_commit"):
        assert result[key] == state[key]


def test_the_executing_server_root_is_the_plugin_directory():
    """FR-017 / AC-025 — the root is derived from ``foundry_mcp.__file__``, not
    from project_root: the whole point of the preflight is that those two can
    differ, so deriving the executing root from the TARGET would compare the
    working tree against itself and pass every time.

    Not monkeypatched — this is the real derivation, checked against the real
    tree this suite runs from."""
    root = F._executing_server_root()

    assert (root / ".claude-plugin" / "plugin.json").is_file(), root
    assert (root / "mcp-server" / "src" / "foundry_mcp" / "__init__.py").is_file()
    assert F._plugin_manifest_version(root), "the real manifest has a version"


# --- AC-026 / ST-009 / OT-017: the version mismatch --------------------------
def test_a_version_mismatch_refuses_naming_version_and_both_values(server, tmp_path):
    """ST-009 verbatim: 'Foundry-Init detects a self-targeting run whose
    executing server version or commit differs from the working tree ... the
    refusal names the reason and the exact launch command.'"""
    _write_plugin_manifest(tmp_path, name="foundry", version="4.10.0")

    result = F.foundry_init(project_root=str(tmp_path))

    assert result["ok"] is False, result
    assert result["mismatch"] == "version"
    assert "version" in result["error"]
    assert "4.10.0" in result["error"], "the working tree's version is not named"
    assert "4.9.0" in result["error"], "the executing server's version is not named"
    assert result["hint"]
    assert result["self_target"] is True


def test_the_refusal_carries_the_exact_launch_command(server, tmp_path):
    """OT-017 verbatim: '... is refused with a message containing the claude
    --plugin-dir launch command.' AC-029's convention, made actionable at the
    moment it is needed: the remedy is a RELAUNCH, so the command has to be
    right there rather than in a document the operator would have to find."""
    plugin_dir = _write_plugin_manifest(tmp_path, name="foundry", version="4.10.0")

    result = F.foundry_init(project_root=str(tmp_path))

    assert result["launch_command"].startswith("claude --plugin-dir ")
    assert result["launch_command"].endswith(str(plugin_dir)), (
        "the command must name the directory holding the matched manifest"
    )


def test_a_top_level_manifest_is_matched_too(server, tmp_path):
    """FR-018 verbatim: 'Foundry-Init looks for
    plugins/*/.claude-plugin/plugin.json (or .claude-plugin/plugin.json) under
    project_root'. A checkout of the plugin ALONE is the other shape a
    self-targeting run arrives in, and its launch command names project_root
    itself."""
    _write_plugin_manifest(tmp_path, name="foundry", version="4.10.0", nested=False)

    result = F.foundry_init(project_root=str(tmp_path))

    assert result["ok"] is False
    assert result["mismatch"] == "version"
    assert result["launch_command"] == f"claude --plugin-dir {tmp_path}"


# --- AC-026 / ST-009: the commit mismatch ------------------------------------
def test_a_commit_mismatch_refuses_naming_commit_and_both_values(server, tmp_path):
    """ST-009 — 'version OR commit'. The version agreeing is the DANGEROUS
    case: a released version number is stable across many commits, so the
    executing build can be arbitrarily far behind the working tree while
    declaring the same version. The commit is what actually pins the build."""
    _write_plugin_manifest(tmp_path, name="foundry", version="4.9.0")

    result = F.foundry_init(project_root=str(tmp_path))

    assert result["ok"] is False, result
    assert result["mismatch"] == "commit"
    assert "commit" in result["error"]
    assert TREE_COMMIT in result["error"]
    assert SERVER_COMMIT in result["error"]
    assert result["launch_command"].startswith("claude --plugin-dir ")


def test_matching_version_and_commit_initializes_the_run(server, tmp_path):
    """The preflight must not be a wall. A self-targeting run whose executing
    server IS the working tree is the arrangement GI-004 asks for, and it
    proceeds — recording self_target True so the report can say so."""
    _write_plugin_manifest(tmp_path, name="foundry", version="4.9.0")
    server["commits"][str(tmp_path)] = SERVER_COMMIT

    result = F.foundry_init(project_root=str(tmp_path))

    assert "error" not in result, result
    assert result["self_target"] is True
    assert _state(result)["self_target"] is True


# --- FR-035: unknown is a sentinel, never a match ----------------------------
def test_an_unreadable_server_commit_refuses_rather_than_matching(server, tmp_path):
    """FR-035 verbatim: 'a missing commit is reported as unknown rather than
    treated as a match.'"""
    _write_plugin_manifest(tmp_path, name="foundry", version="4.9.0")
    server["commits"].pop(str(SERVER_ROOT))

    result = F.foundry_init(project_root=str(tmp_path))

    assert result["ok"] is False, result
    assert result["mismatch"] == "commit"
    assert F.UNKNOWN_COMMIT in result["error"]
    assert result["server_commit"] == F.UNKNOWN_COMMIT


def test_two_unknown_commits_are_a_mismatch_not_an_agreement(server, tmp_path):
    """FR-035, the case bare string equality gets WRONG. ``unknown ==
    unknown`` is True, and that is exactly what a server installed outside a
    git tree looks like — the arrangement this preflight exists to catch. Two
    values nobody could read are not evidence that they agree."""
    _write_plugin_manifest(tmp_path, name="foundry", version="4.9.0")
    server["commits"].clear()

    result = F.foundry_init(project_root=str(tmp_path))

    assert result["ok"] is False, result
    assert result["mismatch"] == "commit"
    assert not F._commit_matches(F.UNKNOWN_COMMIT, F.UNKNOWN_COMMIT)


def test_the_real_commit_lookup_reports_unknown_off_a_work_tree(tmp_path):
    """FR-035 — the real ``_git_head``, not the fake. Every failure mode
    collapses to the sentinel: a directory that is not a work tree is the one
    reachable here without uninstalling git."""
    assert F._git_head(tmp_path) == F.UNKNOWN_COMMIT


# --- the refusal leaves nothing behind ---------------------------------------
def test_a_refused_init_creates_no_run_directory(server, tmp_path):
    """The preflight is decided BEFORE any run directory, state.json or
    manifest is written. A refusal that had already made
    foundry-archive/<name>/ leaves the operator relaunching into an archive
    holding a stillborn run they must now identify and delete — and the next
    thing a refused operator does IS relaunch."""
    _write_plugin_manifest(tmp_path, name="foundry", version="4.10.0")

    result = F.foundry_init(project_root=str(tmp_path))

    assert result["ok"] is False
    assert _runs(tmp_path) == [], "a refused init left a run directory behind"


def test_resume_is_not_gated_by_the_preflight(server, tmp_path):
    """The gate is on the NEW-RUN path only, and that is deliberate: ST-009 is
    written about 'F0 init requested' — the creation of a run — and resume is
    the RECOVERY door. The refusal's own remedy is 'relaunch, then resume', so
    a resume that refused on drift would strand an operator from the artifacts
    of a run already on disk at exactly the moment they are reaching for them.
    """
    created = F.foundry_init(project_root=str(tmp_path))
    assert "error" not in created, created

    # Now make the project self-targeting and mismatched, and resume anyway.
    _write_plugin_manifest(tmp_path, name="foundry", version="4.10.0")
    assert F.foundry_init(project_root=str(tmp_path))["ok"] is False

    resumed = F.foundry_init(resume=created["run_name"], project_root=str(tmp_path))
    assert resumed["resumed"] is True, resumed
    assert resumed["run_name"] == created["run_name"]


# --- a corrupt third-party manifest is not this run's problem -----------------
def test_an_unreadable_foreign_manifest_is_skipped_not_refused(server, tmp_path):
    """A manifest that cannot be read has not been SHOWN to be foundry's.
    Refusing every run whose project happens to contain a corrupt third-party
    plugin manifest would let an unrelated file block an unrelated build, so
    the search skips it and carries on."""
    broken = tmp_path / "plugins" / "other" / ".claude-plugin"
    broken.mkdir(parents=True)
    (broken / "plugin.json").write_text("{not json", encoding="utf-8")

    result = F.foundry_init(project_root=str(tmp_path))

    assert "error" not in result, result
    assert result["self_target"] is False


def test_a_corrupt_manifest_beside_a_real_one_does_not_hide_it(server, tmp_path):
    """...and skipping must not become a way to MISS the foundry manifest. The
    search continues past the unreadable one and still matches foundry."""
    broken = tmp_path / "plugins" / "aaa-first" / ".claude-plugin"
    broken.mkdir(parents=True)
    (broken / "plugin.json").write_text("{not json", encoding="utf-8")
    _write_plugin_manifest(tmp_path, name="foundry", version="4.10.0")

    result = F.foundry_init(project_root=str(tmp_path))

    assert result["ok"] is False, result
    assert result["self_target"] is True


def test_an_unreadable_executing_manifest_never_reads_as_a_match(server, tmp_path, monkeypatch):
    """The version half of FR-035's property. An executing server whose own
    plugin.json cannot be read reports "" — and "" never equals a real version,
    so the run refuses rather than passing on an unreadable comparison."""
    _write_plugin_manifest(tmp_path, name="foundry", version="4.9.0")
    server["plugin_version"] = ""

    result = F.foundry_init(project_root=str(tmp_path))

    assert result["ok"] is False, result
    assert result["mismatch"] == "version"
