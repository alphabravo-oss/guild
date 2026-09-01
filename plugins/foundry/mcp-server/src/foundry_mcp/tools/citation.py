"""verify_citations tool — cross-reference spec requirements with PROVE verdicts.

Also the home of the durable-cite grammar and the mechanical symbol-resolution
guard (FR-004 / US-002). The gate that consumes them is
``foundry_handoff.foundry_accept_casting``.

CITE GRAMMAR (AC-005/006/007)
-----------------------------
Two cite forms are valid, and they are validated by different rules:

    path/to/file.py:42          file:line   — the legacy form, still accepted
    path/to/file.py#Symbol      path#Symbol — the durable form

A line hint may sit on either side of the ``#`` (``file.py:42#Symbol`` and
``file.py#Symbol:42`` both parse). **The line component is never judged.**
A ``#Symbol`` cite is valid exactly when its symbol resolves in the named
file, however stale the hint beside it has become — so a moved line alone
produces no finding of any kind, and cite-refresh sweeps are prohibited
without an explicit directive (AC-007).

A ``#Symbol`` whose symbol resolves nowhere is a DEFECT (AC-006), reported by
``unresolved_symbol_cites``.

THE SYMBOL IS JUDGED AS WRITTEN — NEVER TRUNCATED
-------------------------------------------------
A symbol may be dotted (``Class.method``) or kebab-case
(``check-staged-only``), because ``_PATH``'s own extension whitelist admits
``.sh``, ``.yaml``, ``.css``, ``.md`` and friends, where kebab-case is the
NORMAL spelling for shell functions, rule names, doc anchors, CSS classes and
CLI flags. Both spellings parse whole, and the guard reports on the whole
symbol the author wrote.

That is a correctness property, not a convenience. When ``_SYMBOL`` stopped at
the first hyphen, ``guard.sh#check-totally-nonexistent-thing`` parsed as the
symbol ``check``, the trailing ``-totally-nonexistent-thing`` was discarded as
non-cite text, and ``check`` resolved on its own — so the guard answered about
one symbol and reported on another, and a cite that resolves nowhere passed.
The failure was one-directional and always permissive, which is exactly
AC-006's failure direction.

The rule any future change must preserve: a symbol may never end with a
hyphen or word character still sitting next to it, because that is the state
in which the guard checks one symbol and reports on another. The two
separators are therefore treated differently, and deliberately. A trailing
``-`` is kept (``guard.sh#check-`` is the symbol ``check-``, which fails
resolution as written) because a dangling hyphen is malformed spelling, and
shortening it to ``check`` would resolve. A trailing ``.`` is dropped
(``ledger.py#add_defect.`` is the symbol ``add_defect``) because that is a
cite ending a sentence — the common case in a completion report, and one
where the period is punctuation rather than part of the name. Neither is a
truncation: in both, the symbol ends where the author's symbol ends.

RESOLUTION IS MECHANICAL, NOT SEMANTIC
--------------------------------------
``symbol_cite_resolves`` is a whole-word search over the cited file, with a
dotted symbol (``Class.method``) requiring every component to be present. It
is deliberately language-agnostic and deliberately biased toward resolving:
an unresolvable verdict blocks a casting, so a false negative is the
expensive direction and a false positive costs only a cite that a human
reader would have caught anyway.

"Whole word" counts the hyphen as part of a word, for the same reason the
grammar does: ``#check`` must not resolve off a ``check-args`` occurrence any
more than ``#add`` resolves off ``add_defect``. Both are the guard answering
about a symbol the file does not contain.
"""

from __future__ import annotations

import re
from pathlib import Path

from foundry_mcp.parsers.prove import Verdict, parse_prove_report
from foundry_mcp.parsers.spec import extract_requirements

# ---------------------------------------------------------------------------
# Durable-cite grammar (FR-004 / AC-005).
#
# The source-extension whitelist is lifted verbatim from the citation check
# this grammar replaces (foundry_handoff.foundry_accept_casting) so that
# widening the grammar cannot narrow what the gate accepted before.
# ---------------------------------------------------------------------------

_PATH = (
    r"[\w./\-]+\."
    r"(?:py|ts|tsx|js|jsx|go|rs|java|rb|cpp|c|h|hpp|kt|swift|sql|yaml|yml"
    r"|json|md|sh|toml|html|css|scss|vue|svelte|tf|hcl)"
)
# An UNVALIDATED hint. Present in the grammar so a cite carrying one still
# parses; never compared against anything (AC-007).
_LINE_HINT = r":\d+(?:-\d+)?"
# A symbol starts with an identifier character and runs to the last character
# that could still belong to a symbol. Dots and hyphens are INTERIOR
# characters, so `Class.method` and `check-staged-only` each parse whole; a
# trailing `.` does not (sentence punctuation after a cite is not part of it),
# while a trailing `-` does, because a dangling hyphen is malformed spelling
# and must fail resolution as written rather than be silently shortened into a
# symbol that resolves. The tail is one greedy run with a single anchoring
# character class rather than a repeated group, so no input can make it
# backtrack super-linearly.
_SYMBOL = r"[A-Za-z_](?:[\w.\-]*[\w\-])?"

#: ``path/to/file.ext:123`` or ``:123-145`` — the legacy form (AC-005).
FILE_LINE_CITE_PATTERN = re.compile(_PATH + _LINE_HINT, re.IGNORECASE)

#: ``path/to/file.ext#Symbol`` with an optional line hint on either side.
SYMBOL_CITE_PATTERN = re.compile(
    rf"(?P<path>{_PATH})(?:{_LINE_HINT})?#(?P<symbol>{_SYMBOL})(?:{_LINE_HINT})?",
    re.IGNORECASE,
)

#: Either form. The symbol alternative is tried FIRST so that
#: ``file.py:42#Symbol`` is read as one symbol cite rather than as a bare
#: file:line cite with trailing noise.
CITATION_PATTERN = re.compile(
    rf"(?:{_PATH}(?:{_LINE_HINT})?#{_SYMBOL}(?:{_LINE_HINT})?)"
    rf"|(?:{_PATH}{_LINE_HINT})",
    re.IGNORECASE,
)


def iter_symbol_cites(text: str) -> list[dict]:
    """Return every ``path#Symbol`` cite in ``text``, in order, deduplicated.

    Each entry is ``{"cite", "file", "symbol"}``. ``cite`` is the raw matched
    text (line hint included, if the author wrote one); ``file`` and
    ``symbol`` are the two components validity actually turns on.
    """
    seen: set[tuple[str, str]] = set()
    cites: list[dict] = []
    for m in SYMBOL_CITE_PATTERN.finditer(text):
        key = (m.group("path"), m.group("symbol"))
        if key in seen:
            continue
        seen.add(key)
        cites.append(
            {"cite": m.group(0), "file": key[0], "symbol": key[1]}
        )
    return cites


def symbol_cite_resolves(
    file_path: str,
    symbol: str,
    project_root: str = ".",
) -> bool:
    """True when ``symbol`` is present in ``file_path`` as a whole word.

    A dotted symbol (``Class.method``) resolves only when EVERY component is
    present. A missing or unreadable file never resolves. The line component
    of a cite is not an input here and is never consulted (AC-007).

    The word boundary is hyphen-aware: a component resolves only where it is
    flanked by neither a word character nor a hyphen. ``\b`` is not adequate
    here — it treats a hyphen as a boundary, so ``check`` would resolve off
    ``check-args`` and ``foo-`` off ``foo-bar``, which is the guard reporting
    on a symbol the file does not contain.
    """
    parts = symbol.split(".")
    if any(not part for part in parts):
        # An empty component means a malformed symbol (`a..b`). It cannot be
        # searched for — an empty pattern matches everywhere — so it resolves
        # nowhere rather than resolving trivially.
        return False
    target = Path(file_path)
    if not target.is_absolute():
        target = Path(project_root) / file_path
    if not target.is_file():
        return False
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return all(
        re.search(rf"(?<![\w\-]){re.escape(part)}(?![\w\-])", text)
        for part in parts
    )


def unresolved_symbol_cites(text: str, project_root: str = ".") -> list[dict]:
    """Return the ``path#Symbol`` cites in ``text`` that resolve nowhere.

    The mechanical resolution guard of AC-006: every entry returned is a
    DEFECT. A cite whose symbol resolves is absent from the result no matter
    how stale a line hint beside it is (AC-007).
    """
    return [
        cite
        for cite in iter_symbol_cites(text)
        if not symbol_cite_resolves(cite["file"], cite["symbol"], project_root)
    ]


def verify_citations(
    spec_path: str,
    report_path: str,
    strict: bool = False,
    project_root: str = ".",
) -> dict:
    """Cross-reference a spec with a critic report for traceability.

    Args:
        spec_path: Path to LISA spec file.
        report_path: Path to critic report file.
        strict: If True, fail on any uncovered requirement.
        project_root: Project root for resolving relative paths.

    Returns:
        {traceability_matrix[], summary{}, pass}
    """
    root = Path(project_root)

    # Load spec
    spath = root / spec_path if not Path(spec_path).is_absolute() else Path(spec_path)
    if not spath.exists():
        return {"pass": False, "error": f"Spec not found: {spec_path}", "traceability_matrix": [], "summary": {}}
    spec_text = spath.read_text(encoding="utf-8")

    # Load critic report
    rpath = root / report_path if not Path(report_path).is_absolute() else Path(report_path)
    if not rpath.exists():
        return {"pass": False, "error": f"Report not found: {report_path}", "traceability_matrix": [], "summary": {}}
    report_text = rpath.read_text(encoding="utf-8")

    # Parse both
    requirements = extract_requirements(spec_text)
    req_ids = set(requirements.keys())
    verdicts = parse_prove_report(report_text)
    verdict_ids = {v.id for v in verdicts}

    # Build traceability matrix
    matrix: list[dict] = []
    issues: list[str] = []

    # Check every spec requirement has a verdict
    uncovered_reqs: list[str] = []
    for req_id, req in sorted(requirements.items()):
        # Map requirement to VC — simple heuristic: check if any verdict mentions this req
        matching_verdicts = [v for v in verdicts if req_id in v.reasoning or req_id in v.description]
        if matching_verdicts:
            for v in matching_verdicts:
                matrix.append({
                    "requirement_id": req_id,
                    "requirement_text": req.text[:200],
                    "verdict_id": v.id,
                    "verdict": v.verdict.value,
                    "has_code_ref": len(v.code_refs) > 0,
                    "has_spec_cite": len(v.cited_spec_text) > 0,
                    "status": "covered",
                })
        else:
            uncovered_reqs.append(req_id)
            matrix.append({
                "requirement_id": req_id,
                "requirement_text": req.text[:200],
                "verdict_id": None,
                "verdict": None,
                "has_code_ref": False,
                "has_spec_cite": False,
                "status": "uncovered",
            })

    # Check every verdict for completeness
    uncited_verdicts: list[str] = []
    orphan_verdicts: list[str] = []
    for v in verdicts:
        if not v.cited_spec_text and v.verdict != Verdict.VERIFIED:
            uncited_verdicts.append(v.id)
            issues.append(f"{v.id}: non-VERIFIED verdict without spec citation")

        # Check if verdict references any known requirement
        refs_req = any(rid in v.reasoning or rid in v.description for rid in req_ids)
        if not refs_req:
            orphan_verdicts.append(v.id)

    if uncovered_reqs:
        issues.append(f"Requirements without verdicts: {', '.join(uncovered_reqs)}")
    if orphan_verdicts:
        issues.append(f"Verdicts not linked to requirements: {', '.join(orphan_verdicts)}")

    # Summary
    total_reqs = len(requirements)
    covered = total_reqs - len(uncovered_reqs)
    total_verdicts = len(verdicts)
    verified_count = sum(1 for v in verdicts if v.verdict == Verdict.VERIFIED)

    summary = {
        "total_requirements": total_reqs,
        "covered_requirements": covered,
        "uncovered_requirements": len(uncovered_reqs),
        "coverage_pct": f"{covered / total_reqs * 100:.0f}%" if total_reqs > 0 else "N/A",
        "total_verdicts": total_verdicts,
        "verified_verdicts": verified_count,
        "non_verified_verdicts": total_verdicts - verified_count,
        "uncited_verdicts": len(uncited_verdicts),
        "orphan_verdicts": len(orphan_verdicts),
        "issues": issues,
    }

    # Pass/fail
    passed = True
    if strict and uncovered_reqs:
        passed = False
    if uncited_verdicts:
        passed = False

    return {
        "pass": passed,
        "traceability_matrix": matrix,
        "summary": summary,
    }
