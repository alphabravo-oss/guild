"""AC-040 / FR-027 — the five release-version sites agree.

The release number is written down in FIVE places and DERIVED in none:

  server number (1.10.0)
    * ``src/foundry_mcp/__init__.py``               ``__version__``
    * ``pyproject.toml``                            ``[project] version``
    * ``uv.lock``                                   the ``foundry-mcp`` entry
  plugin number (4.11.0)
    * ``plugins/foundry/.claude-plugin/plugin.json``  ``version``
    * ``.claude-plugin/marketplace.json``             the ``foundry`` entry

WHY THIS FILE EXISTS (AC-040)
-----------------------------
Nothing in the tree joins the two halves, and nothing joins the three server
sites to each other. ``__version__`` is read by exactly one importer
(``server.py``'s ``--version`` action and, from 4.10.0, the MCP handshake);
``pyproject.toml``'s copy is read by hatchling; ``uv.lock``'s copy is written
by a SEPARATE command (``uv lock``) that an operator has to remember to run
AFTER editing ``pyproject.toml``; and the two plugin manifests are read only
by the plugin loader and the marketplace installer, neither of which runs in
CI. So a partial bump is silent at every stage a release passes through: the
suite is green, the server starts, the plugin loads, and the disagreement
surfaces as an operator installing "4.11.0" and being served a manifest that
says something else.

This module is the join. It reads all five sites off disk (the sixth,
``__version__``, by importing the package) and fails when any two disagree.

WHY A PARAMETRISED SITE TABLE RATHER THAN FIVE FUNCTIONS
--------------------------------------------------------
The failure has to NAME the site, because "the versions disagree" sends the
reader back to hunt five files by hand -- which is the same hunt that produced
the disagreement. Parametrising over ``RELEASE_SITES`` puts the site's label
in the pytest test id, so the runner names it on the summary line before
anyone reads the assertion message. Five hand-written functions would carry
five hand-copied failure strings free to drift apart, and a single dict-equality
assertion would bury the site inside a diff blob with no per-site id. The table
also makes the roster DATA: a sixth site is a row, not a remembered edit.

WHY THE SITES ARE READ STRUCTURALLY, NOT BY REGEX
-------------------------------------------------
``tomllib`` is stdlib under this project's ``requires-python = ">=3.12"``, so
``pyproject.toml`` and ``uv.lock`` are parsed rather than pattern-matched. A
regex over ``uv.lock`` would match the FIRST ``version =`` line in a 660-line
file that contains one per package -- and would silently start matching a
different package the day uv reorders its output.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Callable, Mapping

import pytest

from foundry_mcp import __version__

# tests/test_release_version.py -> [0]=tests, [1]=mcp-server, [2]=foundry,
# [3]=plugins, [4]=repo-root. Mirrors test_vocab.py's precedent; two of the
# five sites live ABOVE mcp-server/, so the plugin root is not enough.
REPO_ROOT = Path(__file__).resolve().parents[4]
MCP_SERVER = REPO_ROOT / "plugins" / "foundry" / "mcp-server"

PYPROJECT = MCP_SERVER / "pyproject.toml"
UV_LOCK = MCP_SERVER / "uv.lock"
PLUGIN_MANIFEST = REPO_ROOT / "plugins" / "foundry" / ".claude-plugin" / "plugin.json"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"

# The release declaration. These two literals ARE the release; every site is
# judged against them, and against each other.
SERVER_VERSION = "1.10.0"
PLUGIN_VERSION = "4.11.0"

# The dependency ceiling that keeps the bundled server on the 1.x protocol
# library. server.py uses mcp's 1.x low-level decorator API, which 2.0.0
# removed, so a lock that resolved mcp 2.x is a server that crashes on import.
# Relocking for a version bump must not move it -- pinned here because the
# relock is the step where it would move as a side effect, unnoticed.
MCP_SPECIFIER = ">=1.0.0,<2.0.0"


def _read(path: Path) -> str:
    """Read and decode as ONE operation, naming the file when it is not there.

    A missing site is a disagreement like any other -- it means the release
    moved a file the packaging still points at -- so it fails loudly with the
    resolved absolute path rather than skipping or reading as an empty version.
    """
    if not path.exists():
        raise AssertionError(
            f"release-version site is missing from the tree: {path}"
        )
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> dict:
    return json.loads(_read(path))


def _read_toml(path: Path) -> dict:
    return tomllib.loads(_read(path))


def _uv_lock_foundry_package() -> dict:
    """The ``foundry-mcp`` entry of uv.lock, selected by name."""
    lock = _read_toml(UV_LOCK)
    for package in lock.get("package", []):
        if package.get("name") == "foundry-mcp":
            return package
    raise AssertionError(
        f"{UV_LOCK} has no [[package]] entry named 'foundry-mcp' -- the lock "
        f"no longer describes this project"
    )


def package_version() -> str:
    """The server version as the running interpreter sees it."""
    return __version__


def pyproject_version() -> str:
    return _read_toml(PYPROJECT)["project"]["version"]


def uv_lock_version() -> str:
    return _uv_lock_foundry_package()["version"]


def plugin_manifest_version() -> str:
    return _read_json(PLUGIN_MANIFEST)["version"]


def marketplace_foundry_version() -> str:
    """The foundry entry of the marketplace, selected by name.

    Selected by name and not by index: the marketplace lists thirteen plugins
    on their own release cadences, and a positional read would start reporting
    a neighbour's version the day an entry is inserted above foundry's.
    """
    entries = _read_json(MARKETPLACE)["plugins"]
    for entry in entries:
        if entry.get("name") == "foundry":
            return entry["version"]
    raise AssertionError(
        f"{MARKETPLACE} has no plugin entry named 'foundry'"
    )


# label -> (reader, the release literal that site must carry). The label is
# what the runner prints, so it names the file AND the key within it.
RELEASE_SITES: Mapping[str, tuple[Callable[[], str], str]] = {
    "src/foundry_mcp/__init__.py (__version__)": (package_version, SERVER_VERSION),
    "mcp-server/pyproject.toml ([project] version)": (pyproject_version, SERVER_VERSION),
    "mcp-server/uv.lock (foundry-mcp package entry)": (uv_lock_version, SERVER_VERSION),
    "plugins/foundry/.claude-plugin/plugin.json (version)": (
        plugin_manifest_version,
        PLUGIN_VERSION,
    ),
    ".claude-plugin/marketplace.json (foundry entry version)": (
        marketplace_foundry_version,
        PLUGIN_VERSION,
    ),
}


def disagreeing_sites(readings: Mapping[str, str], expected: str) -> list[str]:
    """One message per site whose reading differs from ``expected``.

    Pure, so the "the failure names the site" property is testable without
    mutating the tree -- see ``test_disagreement_message_names_every_offending_site``.
    Returning a LIST rather than raising means a bump that missed three sites
    reports all three, instead of sending the operator round the loop once per
    site.
    """
    return [
        f"{site} reads {actual!r}, expected {expected!r}"
        for site, actual in readings.items()
        if actual != expected
    ]


@pytest.mark.parametrize(
    ("site", "reader", "expected"),
    [(site, reader, expected) for site, (reader, expected) in RELEASE_SITES.items()],
    ids=list(RELEASE_SITES),
)
def test_site_carries_the_declared_release(
    site: str, reader: Callable[[], str], expected: str
) -> None:
    """AC-040: every one of the five sites carries the release it declares."""
    problems = disagreeing_sites({site: reader()}, expected)
    assert not problems, "; ".join(problems)


def test_pyproject_version_equals_package_version() -> None:
    """AC-040: pyproject.toml agrees with ``__version__``.

    Stated as agreement rather than as a second literal comparison so that a
    FUTURE release which bumps the literals above still fails here when only
    one of the two server files moved.
    """
    problems = disagreeing_sites(
        {"mcp-server/pyproject.toml ([project] version)": pyproject_version()},
        package_version(),
    )
    assert not problems, "; ".join(problems)


def test_uv_lock_version_equals_package_version() -> None:
    """AC-040: the regenerated lock agrees with ``__version__``.

    This is the site a release actually loses: ``uv lock`` is a separate
    command run after the pyproject edit, so the lock is the copy left behind.
    """
    problems = disagreeing_sites(
        {"mcp-server/uv.lock (foundry-mcp package entry)": uv_lock_version()},
        package_version(),
    )
    assert not problems, "; ".join(problems)


def test_marketplace_entry_equals_plugin_manifest_version() -> None:
    """AC-040: the marketplace listing agrees with the plugin manifest."""
    problems = disagreeing_sites(
        {".claude-plugin/marketplace.json (foundry entry version)": marketplace_foundry_version()},
        plugin_manifest_version(),
    )
    assert not problems, "; ".join(problems)


def test_relocking_did_not_move_the_mcp_dependency_cap() -> None:
    """AC-040: the 1.x ceiling is exactly what it was before the bump.

    Both copies are checked. ``pyproject.toml``'s is the one a hand-edit for
    the version line can clip; ``uv.lock``'s is the one a relock can widen.
    They fail independently, so neither stands in for the other.
    """
    declared = _read_toml(PYPROJECT)["project"]["dependencies"]
    assert f"mcp{MCP_SPECIFIER}" in declared, (
        f"{PYPROJECT} no longer declares mcp{MCP_SPECIFIER}; it declares {declared!r}. "
        f"server.py uses mcp's 1.x decorator API, which 2.0.0 removed."
    )

    requires_dist = _uv_lock_foundry_package()["metadata"]["requires-dist"]
    locked = {entry["name"]: entry.get("specifier") for entry in requires_dist}
    assert locked.get("mcp") == MCP_SPECIFIER, (
        f"{UV_LOCK} records mcp {locked.get('mcp')!r}, expected {MCP_SPECIFIER!r}. "
        f"The cap moved as a side effect of relocking."
    )


def test_disagreement_message_names_every_offending_site() -> None:
    """AC-040: a partial bump is reported BY NAME, per site.

    Driven with a synthetic reading table rather than by editing the tree, so
    the guard runs in the ordinary green suite and cannot leave a half-reverted
    version behind if it fails part-way. What is asserted is the property the
    acceptance criterion actually needs: the operator is told WHICH site
    disagrees, not merely that something does.
    """
    readings = {label: SERVER_VERSION for label in RELEASE_SITES}
    stale = "mcp-server/uv.lock (foundry-mcp package entry)"
    readings[stale] = "1.8.0"

    problems = disagreeing_sites(readings, SERVER_VERSION)

    assert len(problems) == 1, problems
    assert stale in problems[0]
    assert "1.8.0" in problems[0]
    assert SERVER_VERSION in problems[0]

    # And an agreeing table is silent -- otherwise the check above would pass
    # on a helper that names every site unconditionally.
    assert disagreeing_sites(
        {label: SERVER_VERSION for label in RELEASE_SITES}, SERVER_VERSION
    ) == []
