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
about a symbol the file does not contain. The grammar and the resolver read
ONE character class (``_SYMBOL_CHAR``) so they cannot drift apart about it.

WHAT A SEPARATOR IS, AND WHY THE ANSWER IS NOT ASCII
----------------------------------------------------
The rule above is not about hyphens; it is about SEPARATORS, and every
non-ASCII look-alike of one counts. An emoji, a non-breaking space, a
non-breaking hyphen (U+2011) and an en-dash (U+2013) are none of them word
characters, so each used to END a symbol — and ``add😀defect`` was read as
``add``, which resolved off the real ``add``. The whole non-ASCII range is
therefore part of a symbol, which turns those cases into refusals (the symbol
parses whole and resolves nowhere) instead of silent truncations. For the same
reason a symbol may BEGIN with any Unicode letter: ``#Аdd`` in Cyrillic used to
produce no cite at all, and a cite the guard cannot see is a cite it cannot
refuse.

SPACES IN PATHS, AND THE LIMIT OF THE RULE
------------------------------------------
``_PATH`` admits no space, so ``docs/my file.py#Sym`` matched only the tail —
and the guard resolved that against a root-level ``file.py`` the author never
named. Whether a space belongs to a path is not decidable from characters
alone (``see hooks/guard.sh`` has the same shape), so the recovery keys on the
one token immediately to the left: if it carries a ``/`` and is not itself a
complete path, the author's path ran through the space. One token is the
deliberate width — two would re-admit ``see plugins/foundry and src/x.py#S`` —
so a path with two interior spaces is not recovered.

NO PATTERN HERE MAY BACKTRACK SUPER-LINEARLY
--------------------------------------------
``unresolved_symbol_cites`` runs the grammar over an ENTIRE completion report,
so a pattern that is quadratic in line length is a denial of service on the
acceptance gate, reachable from an ordinary report that happens to quote a
base64 blob or a minified dump. ``_PATH``'s run is length-bounded for exactly
that reason (see ``_PATH_MAX``); ``_SYMBOL``'s tail is one greedy run with a
single anchoring class rather than a repeated group. Any future widening of
either must preserve the property, and the perf test that pins it uses a hard
timeout rather than a threshold so it cannot rot into a slow pass.
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

# The length ceiling is a ReDoS bound, not a style rule (D-110). `[\w./\-]+`
# can match a `.` itself, so on a long unbroken run of path characters that
# never reaches a valid extension the engine backtracks the `+` across every
# split point, at every start position, and the whole scan goes quadratic:
# 7,750 B measured 131 ms, 15,500 B 512 ms, 31,000 B 2.1 s, extrapolating to
# roughly two hours on a 1 MB line. `unresolved_symbol_cites` runs this over an
# entire completion report, so ONE base64 blob, minified dump or data URI on a
# single line hung the acceptance gate. Size alone was never the trigger — line
# length was, because a newline resets the run.
#
# Bounding the quantifier caps the backtracking at each start position to a
# constant, which makes the scan linear in the report's length. 240 is chosen
# to exceed any real cite (the longest path in this repo is under 90
# characters) by a wide margin while staying far below the lengths where the
# quadratic term bites, and it leaves room for the spaced-path recovery in
# `iter_symbol_cites` below. A path longer than the bound is not rejected; the
# match simply starts later within it.
_PATH_MAX = 240
_PATH = (
    r"[\w./\-]{1,%d}\."
    r"(?:py|ts|tsx|js|jsx|go|rs|java|rb|cpp|c|h|hpp|kt|swift|sql|yaml|yml"
    r"|json|md|sh|toml|html|css|scss|vue|svelte|tf|hcl)"
) % _PATH_MAX
# An UNVALIDATED hint. Present in the grammar so a cite carrying one still
# parses; never compared against anything (AC-007).
_LINE_HINT = r":\d+(?:-\d+)?"
# Every character that may sit INSIDE or at the END of a symbol, in one place,
# because the grammar and the resolver must agree about where a symbol stops.
# When they disagreed, the guard checked one symbol and reported on another.
#
# The non-ASCII range is what closes D-113. `\w` is Unicode-aware, so it
# already admitted Cyrillic and accented LETTERS — but an emoji (So), a
# non-breaking space (Zs), a non-breaking hyphen U+2011 and an en-dash U+2013
# (Pd) are none of them word characters, so each one ENDED the symbol and the
# guard silently read `add😀defect` as `add`, which resolved off the real
# `add`. That is the ASCII-hyphen bug D-054 fixed, reintroduced for every
# non-ASCII look-alike of a separator. Admitting the whole non-ASCII range
# means such a symbol now parses WHOLE and fails resolution as written — a
# refusal instead of a silent truncation, which is the direction AC-006
# requires. `\s` cannot express this: Python's `\s` matches U+00A0, so
# excluding whitespace by class would have left the NBSP case truncating.
_SYMBOL_CHAR = r"[\w\-\u0080-\U0010FFFF]"

# A symbol starts with an identifier character and runs to the last character
# that could still belong to a symbol. Dots and hyphens are INTERIOR
# characters, so `Class.method` and `check-staged-only` each parse whole; a
# trailing `.` does not (sentence punctuation after a cite is not part of it),
# while a trailing `-` does, because a dangling hyphen is malformed spelling
# and must fail resolution as written rather than be silently shortened into a
# symbol that resolves. The tail is one greedy run with a single anchoring
# character class rather than a repeated group, so no input can make it
# backtrack super-linearly.
#
# The START class is `[^\W\d]` — any Unicode letter or underscore, never a
# digit. `[A-Za-z_]` matched no non-ASCII letter at all, so a symbol beginning
# with one (Cyrillic `Аdd`) produced ZERO cites: the guard never examined it,
# and a bogus cite alongside a valid one was silently dropped rather than
# refused. Invisible is worse than wrong here — a cite the guard cannot see is
# a cite it cannot refuse.
_SYMBOL = rf"[^\W\d](?:[\w.\-\u0080-\U0010FFFF]*{_SYMBOL_CHAR})?"

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


# A path token that ran into a space (D-112). `_PATH` admits no space, so
# `docs/my file.py#RealSym` matched only the tail `file.py#RealSym` — and the
# guard then resolved that against a root-level `file.py` that the author never
# named, so a cite to a path which does not exist PASSED. The failure was
# one-directional and always permissive, which is the class the module
# docstring says must never recur.
#
# Whether a space belongs to a path is not decidable from the characters alone:
# `see hooks/guard.sh` and `docs/my file.py` are lexically identical shapes. The
# one signal that separates them is the token IMMEDIATELY to the left of the
# match — `docs/my` carries a `/`, and the English words that precede a cite in
# prose (`see`, `in`, `at`, `and`, `from`) do not. So the lookback is exactly
# one token wide and requires a separator in it. Widening it to two tokens
# would re-admit the false positive (`see plugins/foundry and src/x.py#S`),
# which is why it stays at one; a path with two interior spaces is not
# recovered, and that is the deliberate limit of the rule.
_TRUNCATED_PATH_RE = re.compile(r"(?<![^\s])(\S*/\S*)[ \t]\Z")

#: A left-hand token that is ITSELF a complete cite is a separate cite, not the
#: head of a spaced path. Without this, `src/a.py src/b.py#Sym` and
#: `src/a.py#Sym src/b.py#Sym` — two cites with only a space between them —
#: would join into one bogus path and turn valid cites into findings. The
#: optional `#Symbol` tail is what covers the second shape.
_SELF_CONTAINED_CITE_RE = re.compile(
    rf"\A{_PATH}(?:{_LINE_HINT})?(?:#{_SYMBOL}(?:{_LINE_HINT})?)?\Z",
    re.IGNORECASE,
)

#: Punctuation that ends a token in prose without belonging to the path. A
#: trailing comma is the common one — `src/a.py#Sym, src/b.py#Sym` — and
#: without stripping it the left token stops looking self-contained and the two
#: cites join.
_TOKEN_PUNCTUATION = ",;:.!?)]}>\"'`"


def _authored_path(text: str, match_start: int, path: str) -> str:
    """The path the AUTHOR wrote, when a space truncated the matched one.

    Returns ``path`` unchanged in every ordinary case. When the token
    immediately left of the match contains a ``/`` and is not a complete path
    in its own right, the author's path ran through a space, and the joined
    span is returned instead — so the cite is judged against the file they
    actually named rather than silently re-rooted onto whatever the tail
    happens to match.
    """
    prefix = text[max(0, match_start - _PATH_MAX):match_start]
    m = _TRUNCATED_PATH_RE.search(prefix)
    if m is None:
        return path
    head = m.group(1).rstrip(_TOKEN_PUNCTUATION)
    # A `#` in the left token means it is a cite of its own, whatever
    # punctuation trails it — never the head of a path that ran through a space.
    if "#" in head or _SELF_CONTAINED_CITE_RE.match(head):
        return path
    return f"{head} {path}"


def iter_symbol_cites(text: str) -> list[dict]:
    """Return every ``path#Symbol`` cite in ``text``, in order, deduplicated.

    Each entry is ``{"cite", "file", "symbol"}``. ``cite`` is the raw matched
    text (line hint included, if the author wrote one); ``file`` and
    ``symbol`` are the two components validity actually turns on.

    ``file`` is the path the author WROTE, which is not always the path the
    regex matched — see ``_authored_path`` for the spaced-path case.
    """
    seen: set[tuple[str, str]] = set()
    cites: list[dict] = []
    for m in SYMBOL_CITE_PATTERN.finditer(text):
        key = (_authored_path(text, m.start(), m.group("path")), m.group("symbol"))
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

    The word boundary is ``_SYMBOL_CHAR`` — THE SAME class the grammar uses to
    decide where a symbol ends. ``\b`` is not adequate here: it treats a hyphen
    as a boundary, so ``check`` would resolve off ``check-args`` and ``foo-``
    off ``foo-bar``, which is the guard reporting on a symbol the file does not
    contain.

    Sharing the one class is the point, not an economy. Both D-054 and D-113
    were the grammar and this function disagreeing about which characters end a
    symbol: the grammar stopped at a separator, this function then found the
    truncated stem as a whole word, and the cite passed. Any character the
    grammar would have absorbed into the symbol must therefore also block
    resolution here, or the pair drifts apart again the next time either side
    is widened alone.
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
        re.search(
            rf"(?<!{_SYMBOL_CHAR}){re.escape(part)}(?!{_SYMBOL_CHAR})", text
        )
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
