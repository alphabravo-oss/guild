"""Per-session foundry run state, and the guarded reads every module shares.

Each MCP server process (= each Claude Code session) holds its own
_active_run_name in module-level state. Concurrent sessions on the same
repo don't conflict because each has its own server process.

All foundry runs live under ARCHIVE_DIR at the project root.

# --------------------------------------------------------------------------- #
# D-137 — THE READ AND THE DECODE ARE ONE OPERATION.
#
# Fourteen sites across seven modules wrote
# ``json.loads(path.read_text(encoding="utf-8"))`` under a handler naming
# ``json.JSONDecodeError`` (nine of them adding ``OSError``). None of those
# handlers catch what ``read_text`` itself raises: ``UnicodeDecodeError`` is a
# subclass of ``ValueError``, NOT of ``JSONDecodeError``, and it is raised
# BEFORE ``json.loads`` is ever reached. One non-UTF-8 byte in
# castings/manifest.json therefore raised straight across the MCP boundary from
# both spawn doors -- the exact doors the cycle before had hardened for
# JSON-shape corruption.
#
# The structural half was worse: the package-wide scan that exists to make this
# class unrepresentable COUNTED THOSE SITES AS GUARDED, because it matched
# handler names against a hand-kept frozenset that happened to contain
# ``JSONDecodeError``. A guard whose own membership table is hand-kept is an
# instance of the class it was written to close.
#
# So the primitive lives HERE, in the package's leaf module -- the one thing
# both ``foundry.py`` and ``foundry_orchestrator.py`` already import, and which
# imports nothing from the package itself. Every module can reach it with no
# risk of closing a cycle in the import graph, which is the reason the two
# tolerant loaders were separate copies in the first place.
#
#   ``read_text_file`` — the tolerant core: (text, named problem). Handles the
#                        raise set of the READ: OSError (missing mid-flight, a
#                        directory occupying the name, permissions) and
#                        UnicodeDecodeError.
#   ``read_document``  — ``read_text_file`` plus the decode: adds ValueError
#                        (JSONDecodeError's parent) and the mapping check.
#                        One call, one raise set, nothing left between them.
#
# Neither raises. A caller that must TELL the operator which file is broken
# reports the ``problem`` string, which always NAMES THE FILE.
# --------------------------------------------------------------------------- #
"""

from __future__ import annotations

import json
from pathlib import Path

_active_run_name: str | None = None
ARCHIVE_DIR = "foundry-archive"


def read_text_file(path: Path) -> tuple[str, str | None]:
    """Read UTF-8 text. Returns ``(text, problem)``; never raises.

    ``problem`` NAMES THE FILE when it exists but cannot be read as UTF-8 text,
    else None. An ABSENT file is not a problem — a run legitimately has
    artifacts it has not written yet, and conflating "absent" with "corrupt" is
    what would make a fresh run refuse to start.
    """
    if not path.exists():
        return "", None
    try:
        return path.read_text(encoding="utf-8"), None
    except (OSError, UnicodeDecodeError) as exc:
        return "", f"{path.name} could not be read ({type(exc).__name__}: {exc})"


def read_json(path: Path) -> tuple[object, str | None]:
    """Read parsed JSON of ANY type. Returns ``(value, problem)``; never raises.

    The READ rung, and only that rung. The whole operation — open, decode the
    bytes, parse the text — sits behind ONE call, because every seam between
    those steps is a place a caller's ``except`` clause has historically failed
    to cover (D-137). The raise set is closed here rather than re-decided at
    each call site: ``OSError`` and ``UnicodeDecodeError`` from the read,
    ``ValueError`` (``JSONDecodeError``'s parent) from the parse.

    ``value`` is ``None`` when there is a problem, and otherwise whatever the
    document parsed to — a list, a bare string, ``null``, a number. Callers
    that own a RICHER shape validator than "is it a mapping" use this and hand
    the parsed value to that validator, which names the offending rung
    (``foundry_spawn``'s ``_manifest_shape_error``). Collapsing a wrong-TYPED
    document to ``{}`` here would take that value away from them and answer a
    structural fault with a generic one.
    """
    if not path.exists():
        return None, None
    raw, problem = read_text_file(path)
    if problem is not None:
        return None, problem
    try:
        return json.loads(raw), None
    except ValueError as exc:
        # "parse error" is load-bearing: a torn document must stay
        # distinguishable from a well-formed one of the wrong TYPE, because the
        # two send the operator to look at different things — a syntax fault
        # versus a structural one. `foundry_spawn`'s door tests pin the
        # distinction on this phrase.
        return None, f"{path.name} is not valid JSON — parse error ({exc})"


def read_document(path: Path) -> tuple[dict, str | None]:
    """Read a JSON OBJECT. Returns ``(data, problem)``; never raises.

    ``read_json`` plus the mapping check, for the majority of callers whose
    only shape requirement is "every run artifact is a mapping". ``data`` is
    ``{}`` whenever there is a problem, so a caller that degrades rather than
    refuses can ignore the second element entirely.
    """
    value, problem = read_json(path)
    if problem is not None:
        return {}, problem
    if value is None and not path.exists():
        return {}, None
    if not isinstance(value, dict):
        return {}, (
            f"{path.name} is not a JSON object (found "
            f"{type(value).__name__}) — every run artifact is a mapping"
        )
    return value, None


def document_refusal(path: Path, problem: str) -> dict:
    """The house named refusal for an unreadable document, shaped ONCE.

    Mirrors ``foundry_spawn``'s ``_manifest_shape_error`` exactly — ``ok:
    False``, an ``error`` naming the fault and the file, a ``hint`` naming the
    action — because the two answer the SAME question one rung apart: this one
    when the bytes could not be read, that one when they read fine and the
    shape is wrong. Both spawn doors return this, so the lead cannot learn two
    different stories about one file depending on which door it walked
    through (D-132's property, re-derived one rung down for D-137).
    """
    return {
        "ok": False,
        "error": f"{problem}: {path}",
        "hint": (
            "Repair or delete the named file, then retry. A corrupt run "
            "artifact is never silently overwritten, nor guessed at."
        ),
    }


def set_active_run(name: str) -> None:
    global _active_run_name
    _active_run_name = name


def get_active_run() -> str | None:
    return _active_run_name


def clear_active_run() -> None:
    global _active_run_name
    _active_run_name = None


def get_run_dir(project_root: str, name: str | None = None) -> Path | None:
    """Return the run directory for the given or active run.

    Returns None if no run is active and no name is provided.
    """
    n = name or _active_run_name
    if not n:
        return None
    return Path(project_root) / ARCHIVE_DIR / n
