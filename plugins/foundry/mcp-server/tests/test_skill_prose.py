"""Pins the PROVE AGENT and the four VERIFICATION SKILLS: the population that
decides what a finding IS.

Why a third prose module rather than more assertions in the two that exist.
---------------------------------------------------------------------------
``test_protocol_prose.py`` pins what the NON-PROVE stream agents and the
teammate are told; ``test_lead_prose.py`` pins what the LEAD is told. This one
pins ``agents/assayer.md`` and ``skills/{prove,trace,sight,temper}/SKILL.md`` --
the surfaces that decide, before anything is written to a ledger, whether a
finding is a defect, an observation, or a probe idea nobody has driven yet.

The three populations drift independently and fail for different reasons. A
lead file rots when a tool is added and its row is not; a stream-agent file
rots when a shared rule is reworded in four of five places; these five rot when
one arm of a branch is written and the other is left on the old contract, which
is a failure neither of the other modules is positioned to see. The catch-all
pin the Holmes review grades ``pin-4`` -- organised by defect lineage rather
than by tool -- is what this module declines to grow further: its remedy is
per-population homes, and this is the home for the population above.

Every assertion below answers a concrete failure, and the ones that are not
obvious carry the requirement id and that failure in a comment beside them.

Why substring assertions and not a parser
-----------------------------------------
Inherited from ``test_protocol_prose.py`` and not re-argued here: the pinned
strings are load-bearing English with no schema to validate against, a fuzzy
check would pass on prose saying the opposite, and where several files must
agree on a term the assertion is parametrised across a DERIVED roster so a
partial edit that fixes two of four fails naming the two it forgot. Absence
assertions carry the other half, because a deletion is invisible to every
positive test ever written and prose that CONTRADICTS a pin four bullets later
is invisible to a positive substring check.

Where the vocabulary comes from
-------------------------------
Every closed vocabulary this module pins is READ from
``foundry_mcp.schemas.vocab`` in the ``_PYTEST_DISCOVERY_PHRASE`` shape rather
than re-typed, so a vocabulary change fails on both sides at once: the prose
that still names the old spelling, and the module that still expects it.

What this module does NOT pin, deliberately
-------------------------------------------
The five shared filing bullets. ``agents/assayer.md`` is their source and
``skills/sight/SKILL.md`` and ``skills/temper/SKILL.md`` carry them
byte-identically; ``test_lead_prose.py`` and ``test_protocol_prose.py`` already
hold that, over rosters that include the four non-PROVE stream agents. Pinning
them here as well would put one ruling in three modules free to drift apart,
and the tier bullet in particular is shared with files this casting may not
edit -- so its wording is a cross-casting change, not a local one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
FOUNDRY_ROOT = REPO_ROOT / "plugins" / "foundry"

AGENTS = FOUNDRY_ROOT / "agents"
SKILLS = FOUNDRY_ROOT / "skills"

#: The PROVE agent. It runs as the F2 PROVE stream and again as the F4 ASSAY
#: agent; every ruling this module pins on it belongs to the first identity.
ASSAYER = AGENTS / "assayer.md"

PROVE_SKILL = SKILLS / "prove" / "SKILL.md"
TRACE_SKILL = SKILLS / "trace" / "SKILL.md"
SIGHT_SKILL = SKILLS / "sight" / "SKILL.md"
TEMPER_SKILL = SKILLS / "temper" / "SKILL.md"

#: The five files this module owns, for the floor check below only. Every
#: assertion that follows sweeps a DERIVED roster instead; this tuple exists so
#: that a rename fails HERE, naming the file, rather than as a derivation that
#: quietly returns one member fewer.
PINNED_FILES = (ASSAYER, PROVE_SKILL, TRACE_SKILL, SIGHT_SKILL, TEMPER_SKILL)


def _rel(path: Path) -> str:
    """A pinned file's repo-relative path, for messages."""
    return path.relative_to(REPO_ROOT).as_posix()


def _read(path: Path) -> str:
    """Read a pinned file, failing loudly if it moved or was deleted."""
    assert path.is_file(), (
        f"{_rel(path)} does not exist. If the file was renamed, update this "
        f"module's path constants -- do not delete the assertions, the prose "
        f"they pin is still required."
    )
    return path.read_text(encoding="utf-8")


def _flat(path: Path) -> str:
    """One file with its line wrapping collapsed.

    Every clause pinned below is quoted as it reads, not as the source happens
    to wrap it: ``skills/prove/SKILL.md`` wraps its rules at 88 columns and
    ``agents/assayer.md`` does not wrap at all, so a pin written against either
    file's line breaks would be a pin on the formatter.
    """
    return " ".join(_read(path).split())


@pytest.mark.parametrize("path", PINNED_FILES, ids=_rel)
def test_every_pinned_prose_file_exists(path: Path) -> None:
    """Floor check: this module's population is reachable.

    A parametrised assertion over a file that has moved reports one clear
    failure here instead of five confusing ones further down, and a module
    whose paths have all rotted would otherwise report itself green by
    asserting nothing at all.
    """
    assert _read(path).strip(), (
        f"{_rel(path)} is empty. The rulings this module pins reach their "
        f"stream through this file and through nothing else."
    )
