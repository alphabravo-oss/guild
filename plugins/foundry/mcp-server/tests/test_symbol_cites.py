"""Casting 3 — symbol-anchored cites (US-002 / FR-004).

  AC-005 / OT-006  the acceptance-gate citation check accepts BOTH
                   ``path#Symbol`` and ``file:line``, and a ``#Symbol`` cite
                   whose symbol resolves passes regardless of any stale line
                   hint beside it.
  AC-006 / OT-006  a ``#Symbol`` cite whose symbol does not resolve is flagged
                   as a defect by the mechanical resolution guard.
  AC-007           no verifier judges the line component — a moved line alone
                   produces no finding of ANY kind.

The AC-007 tests are the load-bearing ones. They are written so that they fail
if anyone ever reintroduces line-number comparison, which is the behaviour that
produced the cite-refresh loops this casting exists to end.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from foundry_mcp.tools.citation import (
    CITATION_PATTERN,
    FILE_LINE_CITE_PATTERN,
    SYMBOL_CITE_PATTERN,
    iter_symbol_cites,
    symbol_cite_resolves,
    unresolved_symbol_cites,
)
from foundry_mcp.tools.foundry import foundry_init
from foundry_mcp.tools.foundry_handoff import _hash_str, foundry_accept_casting
from foundry_mcp.tools.foundry_state import clear_active_run

SOURCE = '''"""A module with a couple of real symbols."""


def add_defect(cycle):
    return cycle


class LedgerWriter:
    def flush(self):
        return None
'''

# A shell guard with kebab-case function names — the NORMAL identifier
# spelling in every extension `_PATH` admits beyond Python. `check` appears
# both as a kebab fragment (`check-staged-only`) and as a symbol in its own
# right, which is what makes it the exact D-054/D-057 repro: a cite truncated
# at the first hyphen lands on a prefix that resolves anyway.
SHELL_SOURCE = '''#!/usr/bin/env bash

check-staged-only() {
    git diff --cached --name-only
}

check() {
    check-staged-only
}
'''

# A second guard where `verify` exists ONLY as a kebab fragment.
KEBAB_ONLY_SOURCE = '''#!/usr/bin/env bash

verify-index-only() {
    return 0
}
'''


@pytest.fixture(autouse=True)
def _isolate_active_run():
    clear_active_run()
    yield
    clear_active_run()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A project root containing real source files.

    Two of them are shell, because the durable-cite grammar is not
    Python-only: `_PATH` admits .sh/.yaml/.css/.md, where kebab-case is the
    normal way symbols are spelled.
    """
    src = tmp_path / "src"
    src.mkdir()
    (src / "ledger.py").write_text(SOURCE, encoding="utf-8")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "guard.sh").write_text(SHELL_SOURCE, encoding="utf-8")
    (hooks / "kebab.sh").write_text(KEBAB_ONLY_SOURCE, encoding="utf-8")
    return tmp_path


# --- AC-005: the grammar accepts both forms ---------------------------------
@pytest.mark.parametrize(
    "cite",
    [
        "src/ledger.py:42",
        "src/ledger.py:42-58",
        "src/ledger.py#add_defect",
        "src/ledger.py#LedgerWriter.flush",
        "src/ledger.py#add_defect:42",  # hint AFTER the symbol
        "src/ledger.py:42#add_defect",  # hint BEFORE the symbol
    ],
)
def test_citation_pattern_accepts_both_cite_forms(cite: str) -> None:
    """AC-005 — widening the grammar must not narrow it: every legacy
    file:line form still matches, and the durable path#Symbol form now does
    too, with a line hint permitted on either side."""
    m = CITATION_PATTERN.search(cite)
    assert m is not None, f"grammar rejected {cite!r}"
    assert m.group(0) == cite, f"grammar matched only {m.group(0)!r} of {cite!r}"


def test_legacy_file_line_pattern_is_unchanged() -> None:
    """No-regression — the file:line alternative is carried through verbatim."""
    assert FILE_LINE_CITE_PATTERN.search("plugins/foundry/x.py:12-30")
    assert not FILE_LINE_CITE_PATTERN.search("plugins/foundry/x.py")


def test_symbol_cite_pattern_splits_path_from_symbol() -> None:
    m = SYMBOL_CITE_PATTERN.search("see src/ledger.py:9#LedgerWriter.flush here")
    assert m is not None
    assert m.group("path") == "src/ledger.py"
    assert m.group("symbol") == "LedgerWriter.flush"


def test_symbol_alternative_wins_over_bare_file_line() -> None:
    """``file.py:42#Symbol`` must read as ONE symbol cite, not as a file:line
    cite with trailing noise — otherwise the resolution guard would never see
    the symbol."""
    m = CITATION_PATTERN.search("src/ledger.py:42#add_defect")
    assert m.group(0) == "src/ledger.py:42#add_defect"


def test_iter_symbol_cites_deduplicates_and_keeps_order() -> None:
    text = (
        "AC-1 src/ledger.py#add_defect, AC-2 src/ledger.py#LedgerWriter, "
        "AC-3 src/ledger.py#add_defect again"
    )
    cites = iter_symbol_cites(text)
    assert [c["symbol"] for c in cites] == ["add_defect", "LedgerWriter"]


# --- AC-006: a symbol is judged AS WRITTEN, never truncated (D-054/D-057) ---
@pytest.mark.parametrize(
    ("cite", "symbol"),
    [
        # The two live reproductions from the defect reports, verbatim.
        (
            "plugins/forge/scripts/setup-forge.sh#R3-finalization-rule-18",
            "R3-finalization-rule-18",
        ),
        (
            "hooks/pre-commit-guard.sh#check-totally-nonexistent-thing",
            "check-totally-nonexistent-thing",
        ),
        # Kebab-case in the other extensions `_PATH` admits.
        ("config/app.yaml#database-connection-pool", "database-connection-pool"),
        ("site/main.css#nav-bar-item", "nav-bar-item"),
        # Mixed spellings, and a dangling hyphen that must stay in the symbol
        # rather than be shortened away.
        ("src/ledger.py#Writer.flush-all", "Writer.flush-all"),
        ("hooks/guard.sh#check-", "check-"),
        ("hooks/guard.sh#check--staged", "check--staged"),
    ],
)
def test_kebab_symbol_parses_whole(cite: str, symbol: str) -> None:
    """AC-006 — the grammar must not stop at the first hyphen. Before the fix
    the first case parsed as `R3` and the second as `check`, discarding the
    rest as non-cite text."""
    cites = iter_symbol_cites(cite)
    assert [c["symbol"] for c in cites] == [symbol]
    assert cites[0]["cite"] == cite


@pytest.mark.parametrize(
    "cite",
    [
        "hooks/guard.sh#check-staged-only",
        "src/ledger.py#add_defect",
        "src/ledger.py#LedgerWriter.flush",
        "hooks/guard.sh#check-",
        "src/ledger.py#a..b",
    ],
)
def test_symbol_never_ends_before_a_symbol_character(cite: str) -> None:
    """The structural invariant behind D-054/D-057: silent truncation is
    impossible. Whatever the guard ends up checking, the parse may never stop
    with another symbol character still sitting next to it — that is the state
    in which the guard answers about one symbol and reports on another."""
    cites = iter_symbol_cites(cite)
    assert cites, f"grammar dropped {cite!r} entirely"
    matched = cites[0]["cite"]
    assert cite.startswith(matched)
    remainder = cite[len(matched):]
    assert not re.match(r"[\w\-]", remainder), (
        f"{cite!r} was truncated to {matched!r}, leaving {remainder!r}"
    )


def test_trailing_period_after_a_cite_is_not_part_of_the_symbol(
    tree: Path,
) -> None:
    """No-regression — a cite ending a sentence still parses as the symbol
    alone, so ordinary prose does not manufacture an unresolvable cite."""
    cites = iter_symbol_cites("Implemented at src/ledger.py#add_defect.")
    assert [c["symbol"] for c in cites] == ["add_defect"]
    assert unresolved_symbol_cites(
        "AC-006 src/ledger.py#add_defect.", str(tree)
    ) == []


def test_kebab_symbol_resolves_as_written(tree: Path) -> None:
    """AC-006 — a hyphenated symbol that really is in the file resolves. A
    fix that merely refused hyphens would fail here, and shell functions, rule
    names, doc anchors and CLI flags would have no durable cite at all."""
    assert symbol_cite_resolves("hooks/guard.sh", "check-staged-only", str(tree))
    assert unresolved_symbol_cites(
        "AC-006 hooks/guard.sh#check-staged-only", str(tree)
    ) == []


def test_kebab_symbol_that_does_not_resolve_is_flagged(tree: Path) -> None:
    """AC-006 — and one that is NOT in the file is a defect, reported under
    the name the author actually wrote."""
    assert not symbol_cite_resolves("hooks/guard.sh", "check-unstaged", str(tree))
    unresolved = unresolved_symbol_cites(
        "AC-006 hooks/guard.sh#check-unstaged", str(tree)
    )
    assert [c["symbol"] for c in unresolved] == ["check-unstaged"]


def test_truncation_cannot_hide_a_nonresolving_symbol(tree: Path) -> None:
    """D-054 / D-057 regression, in the exact shape both streams filed.

    `hooks/guard.sh` contains a `check` function, so the truncated prefix
    resolves on its own. Before the fix the guard therefore reported this cite
    clean — it had answered about `check` while reporting on
    `check-totally-nonexistent-thing`. The failure was one-directional and
    always permissive, which is AC-006's failure direction exactly."""
    assert symbol_cite_resolves("hooks/guard.sh", "check", str(tree)), (
        "fixture precondition: the truncated prefix must resolve, or this "
        "test cannot detect truncation"
    )
    unresolved = unresolved_symbol_cites(
        "AC-006 hooks/guard.sh#check-totally-nonexistent-thing", str(tree)
    )
    assert [c["symbol"] for c in unresolved] == ["check-totally-nonexistent-thing"]


def test_bare_prefix_does_not_resolve_off_a_kebab_fragment(tree: Path) -> None:
    """The resolver half of the same root cause: `\\b` treats a hyphen as a
    word boundary, so `verify` used to resolve off `verify-index-only`. A
    hyphen-joined fragment is no more the cited symbol than `add` is
    `add_defect`."""
    assert not symbol_cite_resolves("hooks/kebab.sh", "verify", str(tree))
    assert not symbol_cite_resolves("hooks/kebab.sh", "index", str(tree))
    assert symbol_cite_resolves("hooks/kebab.sh", "verify-index-only", str(tree))


def test_trailing_hyphen_is_not_silently_dropped(tree: Path) -> None:
    """A dangling hyphen is malformed spelling. It must fail resolution as
    written rather than be shortened into `check`, which resolves."""
    unresolved = unresolved_symbol_cites("AC-006 hooks/guard.sh#check-", str(tree))
    assert [c["symbol"] for c in unresolved] == ["check-"]


def test_malformed_dotted_symbol_does_not_resolve(tree: Path) -> None:
    """An empty dotted component cannot be searched for — an empty pattern
    matches everywhere — so it must resolve nowhere rather than trivially."""
    assert not symbol_cite_resolves("src/ledger.py", "add_defect..flush", str(tree))


# --- AC-006: the mechanical resolution guard --------------------------------
def test_resolving_symbol_resolves(tree: Path) -> None:
    assert symbol_cite_resolves("src/ledger.py", "add_defect", str(tree))
    assert symbol_cite_resolves("src/ledger.py", "LedgerWriter", str(tree))


def test_dotted_symbol_requires_every_component(tree: Path) -> None:
    assert symbol_cite_resolves("src/ledger.py", "LedgerWriter.flush", str(tree))
    assert not symbol_cite_resolves(
        "src/ledger.py", "LedgerWriter.rollback", str(tree)
    )


def test_missing_symbol_does_not_resolve(tree: Path) -> None:
    """AC-006 — a symbol that resolves nowhere is a defect."""
    assert not symbol_cite_resolves("src/ledger.py", "no_such_function", str(tree))


def test_missing_file_does_not_resolve(tree: Path) -> None:
    assert not symbol_cite_resolves("src/gone.py", "add_defect", str(tree))


def test_partial_word_does_not_resolve(tree: Path) -> None:
    """Whole-word matching: ``add`` must not resolve just because
    ``add_defect`` is present, or every cite would pass."""
    assert not symbol_cite_resolves("src/ledger.py", "add", str(tree))


def test_unresolved_symbol_cites_reports_only_the_broken_ones(tree: Path) -> None:
    report = (
        "AC-001 src/ledger.py#add_defect\n"
        "AC-002 src/ledger.py#vanished_helper\n"
        "AC-003 src/ledger.py#LedgerWriter.flush\n"
    )
    unresolved = unresolved_symbol_cites(report, str(tree))
    assert [c["symbol"] for c in unresolved] == ["vanished_helper"]
    assert unresolved[0]["file"] == "src/ledger.py"
    assert unresolved[0]["cite"] == "src/ledger.py#vanished_helper"


# --- AC-007: the line component is NEVER judged -----------------------------
@pytest.mark.parametrize("hint", ["", ":1", ":9999", ":4000-4100"])
def test_stale_line_hint_never_affects_validity(tree: Path, hint: str) -> None:
    """AC-007 — ``add_defect`` sits at line 4 of the fixture. A cite claiming
    line 1, line 9999, or no line at all is equally valid, because validity
    turns on the symbol alone. A moved line produces NO finding of any kind."""
    report = f"AC-005 src/ledger.py#add_defect{hint}"
    assert unresolved_symbol_cites(report, str(tree)) == []


def test_stale_hint_before_the_symbol_is_also_ignored(tree: Path) -> None:
    assert unresolved_symbol_cites(
        "AC-005 src/ledger.py:9999#add_defect", str(tree)
    ) == []


def test_resolution_ignores_the_hint_even_when_the_symbol_is_broken(
    tree: Path,
) -> None:
    """The guard's verdict must come from the symbol, so a broken cite is
    reported no matter how plausible its line hint looks."""
    unresolved = unresolved_symbol_cites(
        "AC-006 src/ledger.py:4#gone_away", str(tree)
    )
    assert len(unresolved) == 1
    assert unresolved[0]["symbol"] == "gone_away"


# --- gate wiring ------------------------------------------------------------
def _casting_run(root: Path, spec_requirements: str) -> tuple[str, str, str]:
    """Initialize a run with one casting prompt. Returns (spec, prompt) hashes
    and the run dir path."""
    result = foundry_init(project_root=str(root))
    fdir = Path(result["foundry_dir"])
    spec_text = "# Spec\n\nAC-005 the gate accepts symbol cites.\n"
    (fdir / "spec.md").write_text(spec_text, encoding="utf-8")
    prompt_text = (
        "# Casting 1\n\n"
        f"<spec_requirements>\n{spec_requirements}\n</spec_requirements>\n"
    )
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        prompt_text, encoding="utf-8"
    )
    from foundry_mcp.tools.foundry_handoff import foundry_spec_hash

    spec_hash = foundry_spec_hash(project_root=str(root))["spec_hash"]
    return spec_hash, _hash_str(prompt_text), str(fdir)


def test_gate_accepts_a_symbol_cite_with_a_stale_line_hint(tree: Path) -> None:
    """AC-005 / OT-006 end to end — a report citing ``path#Symbol`` beside a
    badly stale line hint passes the acceptance gate's citation check."""
    spec_hash, prompt_hash, _ = _casting_run(tree, "- **AC-005**: symbol cites")
    result = foundry_accept_casting(
        casting_id=1,
        spec_hash=spec_hash,
        prompt_hash=prompt_hash,
        completion_report="AC-005: implemented at src/ledger.py:9999#add_defect\n",
        project_root=str(tree),
    )
    assert result["missing_citations"] == [], result
    assert result["unresolved_symbol_cites"] == []
    assert result["ok"] is True, result["warning"]


def test_gate_still_accepts_a_legacy_file_line_cite(tree: Path) -> None:
    """AC-005 — no narrowing: the file:line form the gate accepted before is
    still a citation."""
    spec_hash, prompt_hash, _ = _casting_run(tree, "- **AC-005**: symbol cites")
    result = foundry_accept_casting(
        casting_id=1,
        spec_hash=spec_hash,
        prompt_hash=prompt_hash,
        completion_report="AC-005: implemented at src/ledger.py:42-58\n",
        project_root=str(tree),
    )
    assert result["missing_citations"] == []
    assert result["ok"] is True, result["warning"]


def test_gate_flags_an_unresolvable_symbol_cite(tree: Path) -> None:
    """AC-006 / OT-006 — a symbol that resolves nowhere blocks acceptance and
    is named individually."""
    spec_hash, prompt_hash, _ = _casting_run(tree, "- **AC-006**: symbol cites")
    result = foundry_accept_casting(
        casting_id=1,
        spec_hash=spec_hash,
        prompt_hash=prompt_hash,
        completion_report="AC-006: implemented at src/ledger.py#never_written\n",
        project_root=str(tree),
    )
    assert result["ok"] is False
    assert [c["symbol"] for c in result["unresolved_symbol_cites"]] == [
        "never_written"
    ]
    assert "src/ledger.py#never_written" in result["warning"]
    # AC-007 — the rejection must steer AWAY from a cite-refresh sweep.
    assert "do not" in result["warning"].lower()
    assert "cite-refresh" in result["warning"]


def test_kebab_cite_still_counts_as_a_citation() -> None:
    """ADJACENT PATH — `CITATION_PATTERN` is the OTHER consumer of the symbol
    grammar: the gate's missing-citation check (foundry_handoff.py) asks only
    whether a cite is present near a requirement ID, and never resolves
    anything. Widening the symbol is a strict superset, so presence can only
    widen — a kebab cite counted as a citation before (as its truncated
    prefix) and must still count now (whole)."""
    m = CITATION_PATTERN.search("AC-006 see hooks/guard.sh#check-staged-only here")
    assert m is not None
    assert m.group(0) == "hooks/guard.sh#check-staged-only"
    # ...with a stale hint on either side, still one cite (AC-005/AC-007).
    m = CITATION_PATTERN.search("hooks/guard.sh:9999#check-staged-only")
    assert m.group(0) == "hooks/guard.sh:9999#check-staged-only"


def test_gate_accepts_a_resolvable_kebab_symbol_cite(tree: Path) -> None:
    """ADJACENT PATH — the acceptance gate end to end. A real kebab-case
    symbol must not become a false block: the fix has to make the guard
    honest, not merely stricter."""
    spec_hash, prompt_hash, _ = _casting_run(tree, "- **AC-006**: symbol cites")
    result = foundry_accept_casting(
        casting_id=1,
        spec_hash=spec_hash,
        prompt_hash=prompt_hash,
        completion_report=(
            "AC-006: implemented at hooks/guard.sh:9999#check-staged-only\n"
        ),
        project_root=str(tree),
    )
    assert result["missing_citations"] == [], result
    assert result["unresolved_symbol_cites"] == []
    assert result["ok"] is True, result["warning"]


def test_gate_flags_a_truncation_hidden_unresolvable_cite(tree: Path) -> None:
    """ADJACENT PATH — D-054/D-057 at the gate. This report used to be
    accepted, because the guard resolved the `check` prefix. The rejection
    must name the symbol as written."""
    spec_hash, prompt_hash, _ = _casting_run(tree, "- **AC-006**: symbol cites")
    result = foundry_accept_casting(
        casting_id=1,
        spec_hash=spec_hash,
        prompt_hash=prompt_hash,
        completion_report=(
            "AC-006: implemented at hooks/guard.sh#check-totally-nonexistent\n"
        ),
        project_root=str(tree),
    )
    assert result["ok"] is False
    assert [c["symbol"] for c in result["unresolved_symbol_cites"]] == [
        "check-totally-nonexistent"
    ]
    assert "hooks/guard.sh#check-totally-nonexistent" in result["warning"]


def test_gate_reports_a_missing_citation_when_no_cite_of_either_form(
    tree: Path,
) -> None:
    """No-regression — the missing-citation check still fires when the report
    cites nothing at all."""
    spec_hash, prompt_hash, _ = _casting_run(tree, "- **AC-005**: symbol cites")
    result = foundry_accept_casting(
        casting_id=1,
        spec_hash=spec_hash,
        prompt_hash=prompt_hash,
        completion_report="AC-005: I did it, trust me.\n",
        project_root=str(tree),
    )
    assert result["missing_citations"] == ["AC-005"]
    assert result["ok"] is False
