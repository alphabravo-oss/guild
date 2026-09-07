"""Foundry-Fix and Foundry-Sync: the two doors into the defect ledger.

Survey blocks BB and CC. The adjacent-path ladder, the regression-test lane,
the lead-lane measurement, and the filing validation both doors share.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from foundry_mcp.schemas.vocab import (
    DEFECT_SOURCE_IDS,
    DEFECT_TYPES,
    FIX_AUTHORS,
    LEAD_LANE_MAX_FILES,
    LEAD_LANE_MAX_LINES,
    OBSERVATION_CLASSES,
    PYTEST_CONFTEST_BASENAME,
    PYTEST_PYTHON_FILES,
    TIER_UNKNOWN,
    canonical_defect_type,
    defect_tier,
    is_test_file,
)
from foundry_mcp.tools.artifacts import _artifact_guard, _document_transaction
from foundry_mcp.tools.citation import iter_symbol_cites
# fallout FR-025 / CT-019 / ST-006 — the three rules casting 4 exported so this
# door could apply them without a second spelling. `defect_provenance` builds the
# two keys, `fallout_parent_problem` is the one error CT-019 admits, and
# `close_superseded_record` is ST-006's promotion. All three read what they are
# handed and nothing else, which is what lets a batch door call them per finding
# inside its own transaction. Module-top, like `ledger_refusals` beside them:
# this module already imports `foundry.py` and `foundry.py` never imports back.
from foundry_mcp.tools.foundry import (
    close_superseded_record,
    defect_provenance,
    fallout_parent_problem,
    ledger_refusals,
)
from foundry_mcp.tools.foundry_state import (
    current_cycle,
    get_run_dir,
    now_iso,
    read_text_file,
)
from pathlib import Path
from foundry_mcp.tools.orchestration.escalation import DEFECT_CLASS_FIELD




# --- Defect lifecycle ---


# FR-010 / AC-013 — ONE normalisation layer for "is this the defect's own path?"
#
# Both ladders below ask that question, and both used to answer it with raw
# string equality against the two fields a defect record happens to carry. Two
# defects came out of that in one cycle and they are the same shape twice: a
# comparison site left on bytes while the protocol writes something richer.
#
#   D-088  `symbol` carries the durable `path#Symbol` cite form FR-004/AC-005
#          mandate and the four stream agent files instruct — 26 of this run's
#          own 87 records (30%) spell it that way. `_test_ref_name` reduces a
#          reference to a BARE leaf, so the own-symbol rule compared a bare
#          leaf to a path-qualified string and could NEVER be equal: AC-013's
#          distinctness rule was dead for 30% of real filings, and honouring
#          the cite policy was what disabled the fix gate. Driven as a matched
#          pair (same defect, same reference, only the symbol's shape moved):
#          `evict_stale` -> REFUSED, `src/auth/sweeper.py#evict_stale` ->
#          ACCEPTED.
#   D-089  a test INSIDE the defect's own file (`aaa/aaa.py::test_x`) cleared
#          the own-file rule, which compared the WHOLE reference to the WHOLE
#          path, so the `::test_x` suffix was enough to walk past it. And in
#          the other direction `./adjaa/aaa.py::test_x` — a legitimate relative
#          spelling of a genuinely adjacent path — was refused as "a separator
#          that delimits nothing", identically to a reference that really did
#          dangle. Normalisation therefore runs BEFORE the shape ladder, or the
#          adjacent-path answer cannot be written in the form a teammate types.
#
# Every comparison site is bound to these helpers — the reference's file, the
# reference's name, the statement's own-file and own-symbol restatements, and
# the paths a statement names — so a future edit cannot leave one of them on
# raw equality again. That binding is the point: this run's repeated failure is
# never one bad rule, it is one rule fixed in a single copy.
#
# Lexical, never filesystem. `citation.symbol_cite_resolves` exists and is
# deliberately NOT used here, for the same reason the reference ladder refuses
# to stat anything (see its comment below): the run's tests live in a target
# repo at paths this server cannot resolve, and a false refusal blocks a real
# fix behind an unfalsifiable gate. Parsing is shared with the cite grammar
# (`citation.iter_symbol_cites`) rather than re-typed, so `path#Symbol` means
# one thing in this repo.
_LINE_HINT_SUFFIX = re.compile(r":\d+(?:-\d+)?$")


_LEADING_RELATIVE = re.compile(r"^(?:\.{1,2}[/\\])+")


#: Trailing sentence punctuation on a path lifted out of prose — `tools/.` is
#: the directory plus a full stop, not a path segment named ".".
_PATH_TRAILING_PUNCT = ".,;:!?)'\""




def _strip_line_hint(value: str) -> str:
    """Drop a trailing ``:42`` / ``:42-68`` hint. AC-007: never compared."""
    return _LINE_HINT_SUFFIX.sub("", value.strip())




def _normalize_path(value: str) -> str:
    """Fold a path to the single form every own-path comparison judges.

    Drops a line hint and trailing sentence punctuation, folds ``\\`` to ``/``,
    drops a leading ``./`` or ``../``, collapses doubled slashes, drops a
    trailing slash, and casefolds. ``./src/Auth/Session.py:42`` and
    ``src/auth/session.py`` are the same path to this gate, and D-089 is what
    happens when they are not.
    """
    path = _strip_line_hint(value).rstrip(_PATH_TRAILING_PUNCT)
    path = path.replace("\\", "/")
    path = _LEADING_RELATIVE.sub("", path)
    path = re.sub(r"/{2,}", "/", path)
    return path.rstrip("/").casefold()




def _own_symbol_name(own_symbol: str) -> str:
    """The BARE symbol a ``symbol`` field names, whatever shape it was written in.

    ``src/auth/sweeper.py#evict_stale`` -> ``evict_stale``; ``evict_stale`` ->
    ``evict_stale``. D-088: the second spelling was judged and the first was
    not, though FR-004 asks every stream to write the first.
    """
    raw = _strip_line_hint(own_symbol)
    if not raw:
        return ""
    cites = iter_symbol_cites(raw)
    if cites:
        return cites[0]["symbol"]
    # A cite whose extension the grammar does not whitelist still splits at the
    # separator the protocol reserves for exactly this.
    if "#" in raw:
        raw = raw.rsplit("#", 1)[1]
    return _strip_line_hint(raw)




def _own_paths(own_file: str, own_symbol: str) -> set[str]:
    """Every normalised path the defect's own location names.

    The ``file`` field is one. A ``path#Symbol`` ``symbol`` field carries
    another — and on the records where ``file`` was left empty it is the only
    one there is, which is why both fields are read rather than just the
    obvious one.
    """
    paths = {_normalize_path(own_file)} if own_file.strip() else set()
    for cite in iter_symbol_cites(_strip_line_hint(own_symbol)):
        paths.add(_normalize_path(cite["file"]))
    paths.discard("")
    return paths




def _normalize_ref(ref: str) -> str:
    """Strip a leading ``./`` / ``../`` from a reference before it is judged.

    D-089's second half. The empty-segment rule reads a relative prefix as a
    dangling separator, so ``./adjaa/aaa.py::test_x`` was refused identically
    to ``./test`` — the relative spelling of a real adjacent path could not be
    written at all. Only the leading prefix is touched: ``./test`` still fails,
    now on the rule that actually applies to it (it names no location).
    """
    return _LEADING_RELATIVE.sub("", ref.strip())




def _ref_file_component(ref: str) -> str:
    """The FILE a reference names, or a bare qualified-name head.

    ``aaa/aaa.py::test_x`` -> ``aaa/aaa.py``; ``src/auth/s.py#refresh`` ->
    ``src/auth/s.py``; ``auth::sweeper::tests::x`` -> ``auth``, which is not a
    file and simply matches no own path.
    """
    head = ref.split("::", 1)[0]
    if "#" in head:
        head = head.split("#", 1)[0]
    return head




# AC-013 / FR-010 — what makes an `adjacent_path_test` a REAL reference.
#
# The gate examined only the statement, by exact equality against the defect's
# own symbol, and never looked at the test reference at all. Driven and
# accepted before this: "n/a", "TODO", "tested it manually", and a test named
# for the defect's own symbol. A-018 asks for "a reference to a test exercising
# at least one adjacent path" — a string that references no test satisfies the
# gate's letter and none of its purpose.
#
# Structural rules, each decidable from the string alone, each killing values
# observed being accepted:
#
#   1. A reference is a LOCATOR, not a sentence — no internal whitespace.
#      Kills "tested it manually".
#   2. It must carry a locator separator (:: / \ # or .). Kills "TODO".
#   3. It must name a test — a "test" or "spec" token somewhere. Kills "n/a",
#      which clears rule 2 on its slash while referencing nothing.
#   4. Every locator segment must be non-empty — no leading separator, no
#      trailing separator, no doubled separator. Kills "tests/", ".test",
#      "test.", "./test" and "spec.", each of which clears rules 2 and 3 on a
#      separator that delimits nothing (D-050).
#   5. The LEAF must survive normalisation as a name. Take the last part after
#      the qualified-name separators, drop one trailing file extension, strip
#      test/spec scaffolding affixes, and what remains must be a name of at
#      least _TEST_REF_MIN_NAME_CHARS characters that is not a placeholder
#      token. Kills "src/foo.py::test_" (strips to nothing), "x.test",
#      "a.spec", "t.test", "test/x" (one-character names), and
#      "manual-test/none", "foo.test.bar", "no.test.exists" (placeholder and
#      negation names) — all of which cleared rules 1-4 (D-050).
#
# Then one semantic rule, mirroring the statement check's existing philosophy
# (exact equality is the one thing decidable here): the reference must not name
# ONLY the path the defect was found on.
#
# Deliberately NOT a filesystem existence check. The run's tests live in the
# target repo at paths this server cannot resolve reliably — monorepo roots,
# language-specific discovery, tests generated at build time — and a false
# refusal here blocks a real fix behind an unfalsifiable gate. These rules
# reject non-answers; they do not certify that the test exists or passes.
#
# Kept deliberately language-agnostic: foundry runs against Go, JS and Rust
# repos, so `path::name`, `path/to/file.ext`, `Class#method` and dotted module
# paths all clear every rule. Each rule was checked against the cross-language
# accept fixture in tests/test_fix_gate.py before being added — a rule that
# refuses `auth::sweeper::tests::evicts_stale_sessions` or
# `src/auth/__tests__/sweeper.test.ts` is a worse defect than the one it fixes.
_TEST_REF_LOCATOR_CHARS = ("::", "/", "\\", "#", ".")


_TEST_REF_NAMES_A_TEST = re.compile(r"test|spec", re.IGNORECASE)


# The separators that split a QUALIFIED NAME into parts. `.` is excluded: it
# separates a file extension and a dotted module path alike, so the leaf of
# `src/auth/sweeper.spec.ts` is the whole `sweeper.spec.ts`, normalised below.
_TEST_REF_PART_SEPARATORS = ("::", "#", "/", "\\")


# Scaffolding affixes stripped before judging a test's NAME and before
# comparing it to the defect's own symbol, so `test_refresh_session` is
# recognised as naming `refresh_session` and `sweeper.test` as naming
# `sweeper`. `.` joined `_` and `-` here for the dotted JS/TS convention.
_TEST_NAME_AFFIX = re.compile(
    r"^(?:tests?|specs?|it)[_\-.]+|[_\-.]+(?:tests?|specs?)$", re.IGNORECASE
)


# One trailing file extension, dropped before affix stripping: `.py`, `.go`,
# `.ts`, `.rb`. Bounded at 6 characters so a dotted module path's final
# component is not mistaken for an extension.
_TEST_REF_EXTENSION = re.compile(r"\.[A-Za-z0-9]{1,6}$")


_TEST_REF_MIN_NAME_CHARS = 2


# Names that reference nothing. Every one of these was driven through the gate
# and accepted (D-050): `manual-test/none`, `foo.test.bar`, `no.test.exists`.
# Single characters are covered by _TEST_REF_MIN_NAME_CHARS and are not
# repeated here.
_PLACEHOLDER_NAMES = frozenset({
    "aa", "xx", "xxx", "asdf", "blah",
    "foo", "bar", "baz", "qux", "quux",
    "na", "nil", "null", "none", "no", "not", "nope", "nothing", "nada",
    "tbd", "todo", "fixme", "wip", "pending", "unknown", "unclear",
    "manual", "manually", "dummy", "fake", "placeholder", "example",
    "sample", "temp", "tmp",
})  # 34 names




def _locator_segments(ref: str) -> list[str]:
    """Split ``ref`` on every locator separator, ``::`` counting as one."""
    sentinel = "\x00"
    normalized = ref.replace("::", sentinel)
    for sep in ("/", "\\", "#", "."):
        normalized = normalized.replace(sep, sentinel)
    return normalized.split(sentinel)




def _test_ref_name(ref: str) -> str:
    """The bare NAME a reference resolves to, or "" if it names nothing.

    Leaf of the qualified name, minus one trailing file extension, minus
    test/spec scaffolding affixes. ``tests/test_auth.py`` -> ``auth``;
    ``src/auth/sweeper.spec.ts`` -> ``sweeper``; ``src/foo.py::test_`` -> "".
    """
    leaf = ref
    for sep in _TEST_REF_PART_SEPARATORS:
        if sep in leaf:
            leaf = leaf.rsplit(sep, 1)[1]
    leaf = _TEST_REF_EXTENSION.sub("", leaf)
    # Repeat to a fixed point so `sweeper.test` and `spec_helper_test` both
    # reduce, and a doubly-affixed name does not keep half its scaffolding.
    for _ in range(4):
        stripped = _TEST_NAME_AFFIX.sub("", leaf)
        if stripped == leaf:
            break
        leaf = stripped
    return leaf




# FR-010's load-bearing word: the test must drive a NAMED adjacent path — one
# the STATEMENT named. A-017 defines the statement as "who else calls this /
# what else transitions here / what runs concurrently", so the two declarations
# are a matched pair: the statement names the paths, the reference drives one
# of them.
#
# D-092: that coupling did not exist. `_test_ref_problem` took (ref, own_symbol,
# own_file) — the statement was not a parameter and was never read when judging
# the reference — so `foundry_mark_defect_fixed` ran two INDEPENDENT checks and
# never related them. Driven through server.py#_DISPATCH["Foundry-Fix"] against
# a defect on `refresh_session`: statement "login_handler also calls this and
# the sweeper runs concurrently", reference
# "tests/test_billing.py::test_invoice_totals_round_half_up" -> ACCEPTED. The
# statement named two adjacent paths and the referenced test drove neither.
#
# The rule is deliberately the weakest one that closes that: share ONE token
# and the reference is accepted. Refusing a real answer is this gate's
# characteristic failure — it is what D-076 and D-085 both were, and it fires
# in GRIND where the teammate has no way around it — so every judgement call
# here is resolved toward accepting:
#
#   * ANY overlap accepts. Not a majority, not the leading token, one token.
#   * It is the LAST rung, reached only after the whole structural ladder and
#     only when the statement itself cleared its own ladder (see the caller):
#     relating a refused statement would report the reference as unlinked when
#     the real problem is the statement the caller is already being told about.
#   * It judges only a reference that names a test FUNCTION. A reference that
#     names a whole test FILE names a container, and a container's name is not
#     a claim about which path is driven — judging it would be asserting
#     something the reference never said. That is the same ceiling the rules
#     above keep ("these reject non-answers; they do not certify"), and it is
#     why `tests/test_auth.py` stays acceptable against any statement.
#
# The tokens compared are the discriminating ones: locator scaffolding and the
# filler that appears in a path and in a sentence without linking them is
# dropped, because an overlap on "test" or "the" is not evidence of anything.
_LINKAGE_MIN_TOKEN_CHARS = 3


_LINKAGE_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+")


_LINKAGE_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


# Words a locator and a sentence share without the sharing meaning anything:
# test scaffolding, the conventional source directories, this gate's own
# vocabulary (every statement says "calls"/"path"/"concurrently"), and ordinary
# English filler. Dropping a word makes the rule STRICTER, so this set is kept
# to words that genuinely carry no linkage rather than extended for tidiness.
_LINKAGE_STOPWORDS = frozenset({
    # locator scaffolding and conventional roots
    "test", "tests", "testing", "spec", "specs", "src", "lib", "pkg",
    "internal", "cmd", "app", "main", "index",
    # this gate's own vocabulary — present in nearly every statement
    "path", "paths", "adjacent", "defect", "defects", "call", "calls",
    "called", "caller", "callers", "run", "runs", "running", "concurrent",
    "concurrently", "transition", "transitions", "code", "file", "files",
    "function", "functions", "method", "methods", "module", "modules",
    "line", "lines", "name", "named", "names", "check", "checks", "fix",
    "fixed", "branch", "case", "cases",
    # ordinary English filler
    "the", "and", "but", "for", "not", "are", "was", "were", "has", "have",
    "had", "its", "this", "that", "these", "those", "there", "their", "they",
    "them", "with", "from", "into", "onto", "also", "both", "all", "any",
    "some", "more", "most", "only", "just", "then", "than", "when", "where",
    "which", "while", "who", "what", "how", "why", "does", "did", "done",
    "can", "will", "would", "should", "could", "been", "being", "one", "two",
    "still", "same", "other", "others", "another", "each", "every", "via",
    "per", "out", "off", "yet", "now", "new", "old", "use", "used", "uses",
    "using", "here", "else", "way", "ways", "thing", "things",
})  # 127 words




def _content_tokens(text: str) -> set[str]:
    """The discriminating word-parts of ``text``, casefolded.

    Splits on every non-alphanumeric character and again at camelCase
    boundaries, so ``TestSweeperEvictsStale`` and ``test_sweeper_evicts_stale``
    yield the same set. Scaffolding, filler and anything under
    ``_LINKAGE_MIN_TOKEN_CHARS`` characters is dropped.
    """
    parts: list[str] = []
    for chunk in _LINKAGE_TOKEN_SPLIT.split(text):
        if chunk:
            parts.extend(_LINKAGE_CAMEL_BOUNDARY.split(chunk))
    return {
        folded
        for folded in (part.casefold() for part in parts)
        if len(folded) >= _LINKAGE_MIN_TOKEN_CHARS
        and folded not in _LINKAGE_STOPWORDS
    }




def _ref_singles_out_a_leaf(ref: str) -> bool:
    """True when the reference singles out a NAME INSIDE a file, not the file.

    The leaf of the qualified name carries no file extension:
    ``tests/test_auth.py::test_sweeper`` and ``auth::sweeper::tests::evicts``
    do, ``tests/test_auth.py`` and ``sweeper.spec.ts`` do not.

    D-065 — NAMED FOR WHAT IT MEASURES. This was called
    ``_ref_names_a_test_function`` and its whole body is
    ``_TEST_REF_EXTENSION.search(leaf) is None`` — "the leaf has no file
    extension". Nothing here asks whether the target is a TEST, and
    ``_regression_test_problem`` read the old name as though it did: it called
    this rung and NO other, so the LATENT lane accepted
    ``src/auth/session.py::refresh_session`` — the defect's own production
    symbol in its own file — as the regression test that holds the fix. The
    name is the whole of the defect; a predicate whose name overstates it is
    read as a check its caller never made.
    """
    leaf = ref
    for sep in _TEST_REF_PART_SEPARATORS:
        if sep in leaf:
            leaf = leaf.rsplit(sep, 1)[1]
    return _TEST_REF_EXTENSION.search(leaf) is None




def _linkage_problem(ref: str, statement: str) -> str | None:
    """D-092: name why ``ref`` drives no path ``statement`` named, else None.

    A pure string relation between two caller-supplied fields — no I/O, and no
    claim that either names anything real. See the block above for why every
    branch here resolves toward accepting.
    """
    if not statement or not _ref_singles_out_a_leaf(ref):
        return None
    ref_tokens = _content_tokens(ref)
    statement_tokens = _content_tokens(statement)
    if not ref_tokens or not statement_tokens or (ref_tokens & statement_tokens):
        return None
    named = ", ".join(sorted(statement_tokens)[:8])
    drives = ", ".join(sorted(ref_tokens)[:8])
    return (
        f"{ref!r} drives none of the paths the statement named. The statement "
        f"names {named}; the reference names {drives}. FR-010 asks for a test "
        "that drives a NAMED adjacent path — one the adjacent_path_statement "
        "named — so reference the test that drives one of those, or name the "
        "path this test actually drives in the statement"
    )




def _test_ref_problem(
    ref: str,
    own_symbol: str,
    own_file: str,
    statement: str = "",
) -> str | None:
    """Name why ``ref`` is not a usable adjacent-path test reference, else None.

    Returns a reason string suitable for a named refusal. Never raises: every
    branch is a pure string test over the caller's own input. ``statement`` is
    the caller's adjacent-path statement when it has already cleared its own
    ladder, and enables the linkage rung (D-092); passing "" skips that rung.

    The refusals quote the caller's OWN spelling. Normalisation (D-089) decides
    the verdict and must never decide what the caller is shown, or a teammate
    reads a refusal about a string they did not write.
    """
    if any(ch.isspace() for ch in ref):
        return (
            f"{ref!r} is prose, not a test reference. Give a locator such as "
            "tests/test_auth.py::test_sweeper_evicts_stale_sessions."
        )
    # D-089: the relative prefix is dropped BEFORE the shape ladder, so
    # `./adjaa/aaa.py::test_x` is judged as the locator it is. `./test` still
    # fails — on the locator rule immediately below, which is the rule that
    # actually applies to it.
    normalized_ref = _normalize_ref(ref)
    if not any(sep in normalized_ref for sep in _TEST_REF_LOCATOR_CHARS):
        return (
            f"{ref!r} names no location. A test reference carries a path or a "
            "qualified name (path/to/test_file.py::test_name)."
        )
    if not _TEST_REF_NAMES_A_TEST.search(normalized_ref):
        return (
            f"{ref!r} does not name a test. The reference must point at a test "
            "file or test function."
        )
    if any(seg == "" for seg in _locator_segments(normalized_ref)):
        return (
            f"{ref!r} has a separator that delimits nothing — a dangling or "
            "doubled '/', '.' or '::'. A reference names a test, not a "
            "directory or an extension on its own."
        )

    # The only FILE the reference names is the one the defect was found in —
    # whether it stops there or singles out a test within it. D-089: this
    # compared the whole reference to the whole path, so `aaa/aaa.py` was
    # refused and `aaa/aaa.py::test_aaa_adjacent`, a test in the defect's own
    # file, walked straight past on the strength of its suffix.
    own_paths = _own_paths(own_file, own_symbol)
    ref_file = _normalize_path(_ref_file_component(normalized_ref))
    if own_paths and ref_file and ref_file in own_paths:
        return (
            f"{ref!r} names no file but the defect's own ({own_file or ref_file}), "
            "so it drives no path adjacent to the one the defect was found on. "
            "AC-013 asks for a test on a DIFFERENT caller, transition or "
            "concurrent interaction — reference the test that drives it, or "
            "name it as a qualified name rather than a path into this file."
        )

    name = _test_ref_name(normalized_ref)
    if not name:
        return (
            f"{ref!r} is test scaffolding with no test name attached — it "
            "strips to nothing. Name the test, not the prefix."
        )
    if len(name) < _TEST_REF_MIN_NAME_CHARS:
        return (
            f"{ref!r} resolves to {name!r}, which names nothing specific. The "
            "reference must identify a test, not a single letter beside the "
            "word 'test'."
        )
    if name.casefold() in _PLACEHOLDER_NAMES:
        return (
            f"{ref!r} resolves to the placeholder {name!r}, which references "
            "no test. A-018 asks for a test that EXERCISES an adjacent path; "
            "a well-formed string that points at nothing is the same "
            "non-answer as 'n/a'."
        )

    # Compare the reference's NAME to the defect's own symbol. Exact equality
    # only, so a test like test_refresh_session_from_login_handler (a genuinely
    # adjacent caller) still passes while test_refresh_session does not.
    #
    # D-088: both sides are normalised to a bare name first. `own_symbol` was
    # compared verbatim, so a record spelling it as `path/f.py#evict_stale` —
    # the durable form FR-004 mandates and 30% of this run's records use —
    # could never equal a bare leaf, and this rule was inert for exactly the
    # spelling the protocol asks for.
    own_name = _own_symbol_name(own_symbol)
    if own_name and name.casefold() == own_name.casefold():
        return (
            f"{ref!r} names a test for the defect's own symbol "
            f"({own_symbol}). AC-013 requires a test driving a NAMED "
            "adjacent path — a DIFFERENT caller, transition, or concurrent "
            "interaction than the one the defect was found on."
        )

    # Last rung (D-092): the reference must drive a path the STATEMENT named.
    # Reached only for a statement that already cleared its own ladder.
    return _linkage_problem(ref, statement)




# FR-009 / AC-013 — what makes an `adjacent_path_statement` a REAL answer.
#
# D-050: the test-reference ladder above was built and the statement side was
# left exactly as cycle 2 found it — one check, exact equality against the
# defect's own symbol. Driven and ACCEPTED after that fix landed: 'x', 'none',
# 'n/a', 'no adjacent paths', 'the same path', 'nothing', '-', '0'. A
# declaration that there IS no adjacent path satisfied a gate whose entire
# purpose is to make the fixer name one.
#
# A-017 asks the statement to name "who else calls this / what else transitions
# here / what runs concurrently". Three rules, in the order a caller most needs
# to hear them:
#
#   1. It must not restate the defect's own path — its own symbol or its own
#      file (the pre-existing rule, now covering both), nor say so in words:
#      "the same path", "same as the defect".
#   1b. D-085 is that literal rule's own over-correction, and it is D-076 one
#      pattern over. The literal form matched on "same" ALONE, so a statement
#      whose SUBJECT is a shared resource —
#
#          "The same index.lock is taken by the pathspec commit path and by
#           foundry_validate's git query, which is the concurrent interaction."
#
#      — was told it "declares that there is no adjacent path", which is the
#      opposite of what it says. Two distinct real paths meeting at one lock,
#      one file or one record is "what runs concurrently" answered exactly:
#      sharing the resource IS the adjacency. Moving the resource off the front
#      of the sentence was always accepted ("The pathspec commit path and
#      foundry_validate's git query both take index.lock…"), and that is what
#      proved the rule lexical rather than semantic — same two paths, same
#      claim, only the first word moved. The NOUN AFTER "same" is what carries
#      the restatement, so that is what the pattern reads.
#   2. It must not LEAD with a negation. A statement that opens by asserting no
#      other path exists is a refusal to answer, not an answer; note the gate is
#      already unsatisfiable in that case, because a fixer with no adjacent path
#      has no adjacent-path test to reference either. These patterns are
#      ^-anchored, so `test_no_duplicate_ids` inside a longer statement is
#      untouched.
#   2b. A negation LATER in the statement is refused only when it has bounded
#      nothing — see `_unbounded_denial` below. This is D-076, and it is rule
#      2's over-correction: the two adjacency patterns used to be UNANCHORED
#      whole-string searches sitting in the tuple above under a comment
#      claiming "Anchored patterns only", which was false of exactly those two.
#      They therefore refused the MOST rigorous form of the answer A-017 asks
#      for — an enumeration followed by a clause CLOSING it:
#
#          "_current_cycle is also called by foundry_get_context and
#           _format_status_display; no other module reads state.json directly,
#           so those two are the adjacent callers."
#
#      That names two real adjacent callers and then states the radius is
#      closed, and the gate rejected it as "declares that there is no adjacent
#      path" — in GRIND, the phase where every defect must close. The property
#      that separates it from a genuine non-answer is positional and is true of
#      the language rather than of punctuation: A BOUND COMES AFTER WHAT IT
#      BOUNDS. So a trailing denial is an answer when something was named
#      before it, and a refusal when nothing was.
#   3. It must carry enough substance to have named something —
#      _STATEMENT_MIN_WORDS words of at least two letters. Kills 'x', '-', '0',
#      'none', 'n/a', 'nothing', 'no adjacent paths' and 'the same path' on
#      length alone, and is the floor rules 2 and 2b sit on top of.
#
# Like the reference rules, these reject non-answers; they cannot certify that
# the named path is real. That is the ceiling of what a string check can do,
# and the run's own INSPECT streams are what verify the rest. Rule 1's literal
# form shows the ceiling plainly: it catches the restatement that OPENS on
# those two nouns and never caught one buried mid-sentence ("It is the same
# file as the defect" is accepted here, at HEAD and before it). Widening it
# back toward that case is what refuses real answers, which is D-085, so the
# missed non-answer is the side to err on.
_STATEMENT_MIN_WORDS = 4


_STATEMENT_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]+")


_STATEMENT_NON_ANSWERS = (
    # Leading negation: "no other callers", "none", "nothing else touches it",
    # "there are no adjacent paths", "not applicable".
    re.compile(r"^(?:there\s+(?:are|is)\s+)?(?:no|none|not|nothing|never)\b", re.I),
    # Rule 1's literal form: "the same path", "same as the defect". Narrowed
    # from `^(?:the\s+)?same\b` by D-085 — see rule 1b above. It sits in this
    # tuple for the anchoring it needs, not because it is a negation; the two
    # nouns are what restate the defect's own path.
    re.compile(r"^(?:the\s+)?same\s+(?:path|as)\b", re.I),
    re.compile(r"^n\s*/?\s*a$", re.I),
)


# Rule 2b (D-076). A negation of adjacency ANYWHERE in the statement. These are
# deliberately unanchored — a bound is not expected at the start — and are
# judged by `_unbounded_denial`, never by a bare whole-string search.
_STATEMENT_ADJACENCY_DENIALS = (
    re.compile(r"\bno\s+(?:other|adjacent|additional|further)\b", re.I),
    re.compile(r"\bnothing\s+else\b", re.I),
)




def _unbounded_denial(normalized: str) -> bool:
    """True when a denial of adjacency has named nothing for it to bound.

    D-076. The test is the text BEFORE the first denial: a statement that
    enumerated callers and then closed the radius has cleared the same
    substance floor rule 3 applies to the whole statement, while "I found no
    other callers" and "the grep shows no other callers" have not — they open
    with a subject and a verb and then decline to answer.

    Deliberately positional and not a clause tokenizer: splitting English on
    punctuation would have to guess at the '.' inside ``state.json`` and at how
    deep a comma nests, and would still accept "Also, no other module calls
    this" on one word of filler. Counting the words a denial had available to
    bound needs neither guess.
    """
    starts = [
        match.start()
        for match in (pattern.search(normalized) for pattern in _STATEMENT_ADJACENCY_DENIALS)
        if match is not None
    ]
    if not starts:
        return False
    bounded = _STATEMENT_WORD.findall(normalized[: min(starts)])
    return len(bounded) < _STATEMENT_MIN_WORDS




# A path NAMED inside a statement: anything carrying a directory separator, or
# a bare filename with an extension. Used only to ask D-089's question — "is
# every path this statement names the defect's own?" — which is a property of
# the WHOLE declaration and so cannot refuse a statement that also names
# something else. A token this over-matches (`e.g`, `tools/.`) can only make
# the rule fire LESS, which is the direction to be wrong in.
_STATEMENT_NAMED_PATH = re.compile(
    r"(?:[\w.\-]+[/\\])+[\w.\-]*"
    r"|[\w\-]+\.[A-Za-z0-9]{1,6}\b"
)




def _statement_problem(statement: str, own_symbol: str, own_file: str) -> str | None:
    """Name why ``statement`` is not a usable adjacent-path statement, else None."""
    normalized = " ".join(statement.split())
    folded = normalized.casefold()
    # D-088: both own-path comparisons read the normalised forms, so a record
    # whose `symbol` is spelled `path/f.py#refresh_session` is judged the same
    # as one spelled `refresh_session`. The statement side is normalised only
    # when it is a single token — running the cite parser over prose would let
    # a statement that MENTIONS a cite and then names a real adjacent caller
    # compare equal to the defect's own symbol, which is a false refusal and
    # the failure mode this gate has already had twice.
    own_name = _own_symbol_name(own_symbol)
    own_paths = _own_paths(own_file, own_symbol)
    single_token = " " not in normalized
    statement_name = _own_symbol_name(normalized) if single_token else normalized

    if own_name and statement_name.casefold() == own_name.casefold():
        return (
            f"it just names the defect's own symbol ({own_symbol}). An "
            "adjacent path is a DIFFERENT caller, transition, or "
            "concurrent interaction than the one the defect was found on"
        )
    if own_paths and single_token and _normalize_path(normalized) in own_paths:
        return (
            f"it just names the defect's own file ({own_file or normalized}), "
            "which is the path the defect was found on rather than one "
            "adjacent to it"
        )
    for pattern in _STATEMENT_NON_ANSWERS:
        if pattern.search(normalized):
            return (
                f"{normalized!r} declares that there is no adjacent path. That "
                "is a refusal to answer, not an answer — and a fix with no "
                "adjacent path has no adjacent-path test to reference either. "
                "Name who ELSE calls this, what else transitions here, or what "
                "runs concurrently"
            )
    if _unbounded_denial(normalized):
        # D-076: name the REMEDY, which is not "delete the denial". Closing the
        # radius is the strongest form of the answer — it just has to come
        # after the answer it closes.
        return (
            f"{normalized!r} denies that an adjacent path exists without first "
            "naming one, so the denial bounds nothing. A closing clause like "
            '"no other module reads it" is welcome — and is the most rigorous '
            "form of the answer — but it belongs AFTER the enumeration it "
            "closes. Name who ELSE calls this, what else transitions here, or "
            "what runs concurrently, and then bound it"
        )
    if len(_STATEMENT_WORD.findall(normalized)) < _STATEMENT_MIN_WORDS:
        return (
            f"{normalized!r} is too thin to have named a path. State who else "
            "calls this, what else transitions here, or what runs concurrently "
            f"— at least {_STATEMENT_MIN_WORDS} words naming real callers, "
            "transitions or concurrent work"
        )

    # Last rung (D-089): every path the statement names is the defect's own, so
    # however many words it spent, it named no path beside the one the defect
    # was found on. Deliberately a SUBSET test over the whole declaration and
    # not a search: a statement that names the own file alongside another path
    # — "login_handler in src/auth/session.py also calls this" — names a real
    # adjacent caller and is accepted. The lexical `same` pattern above is
    # untouched; widening THAT is D-085, and this rule reaches the mid-sentence
    # restatement it deliberately cannot without reading phrases.
    named_paths = {
        _normalize_path(match) for match in _STATEMENT_NAMED_PATH.findall(normalized)
    }
    named_paths.discard("")
    if own_paths and named_paths and named_paths <= own_paths:
        return (
            f"the only path it names is the defect's own ({own_file or own_symbol}). "
            "Name who ELSE calls this, what else transitions here, or what runs "
            "concurrently — a path beside the one the defect was found on, not "
            "the one it was found on restated"
        )
    return None




# --------------------------------------------------------------------------- #
# The LATENT fix lane (CT-004 / ST-003 / FR-008 / AC-011 / AC-012 / OT-007)
# --------------------------------------------------------------------------- #

#: The two shapes a `def`-line can take for a name the locator points at.
#: A test may be a function (`def test_evicts`) or a method on a `Test` class
#: (`class TestSweeper:` / `def test_evicts`), and both are what pytest collects.
_REGRESSION_DEF_TEMPLATES = ("def {name}", "class {name}", "async def {name}")




def _split_pytest_node_id(ref: str) -> tuple[str, list[str], str]:
    """``(file path, name chain, parameter id)`` for one pytest node id.

    A node id is spelled ``relpath::Name::Name[param id]`` and this splits it
    the way pytest composes it, rather than by looking for separators:

      * the FILE PATH is everything before the FIRST ``::`` — a path may
        contain ``/``, ``.`` and spaces, and none of them ends it;
      * the PARAMETER ID is the whole tail from the FIRST ``[`` to the end,
        and only when the id ends with ``]``. ``[`` cannot occur in a Python
        identifier, so "the name runs up to the first bracket" is the grammar
        and not a heuristic;
      * the NAME CHAIN is what is left between them, split on ``::`` — one
        element for a module-level test, two for a method on a ``Test`` class.
        The TEST's own name is the last element.

    D-105 / D-134 / D-145 — THE ESCALATED CLASS `false-refusal-diagnostic`, AS
    A PARSER RATHER THAN AS STRING SURGERY.
    -------------------------------------------------------------------------
    Three cycles filed the same shape: the LATENT lane refusing a node id
    ``pytest --collect-only`` actually emits, each time because the locator was
    cut with a different pair of string operations. D-134 stripped a trailing
    ``[...]`` — but only AFTER ``name_part.split('::')[-1]`` had already cut
    the id at a ``::`` INSIDE the brackets, and it never taught the
    whitespace rung that a bracket payload is not prose. Both survivors were
    driven at GRIND cycle 8:

        tests/test_fix_gate.py::test_real_test_references_across_languages
            _are_accepted[tests/test_auth.py::test_sweeper_evicts_stale_sessions]
            -> "tests/test_fix_gate.py exists but defines no
               'test_sweeper_evicts_stale_sessions]'"

        tests/test_fix_gate.py::test_a_latent_locator_that_names_no_test_is
            _refused[a::b-two letters either side of a separator]
            -> "is prose, not a locator"

    Both name a test this suite runs. THIRTEEN of this repo's collected node
    ids carry a ``::`` inside their brackets and every one of them guards this
    very lane, so the door refused the tests written to hold it.

    pytest's ``idmaker`` keeps a string parameter verbatim — ``::``, ``/``,
    spaces and brackets included — which is exactly why no amount of further
    cutting terminates. The payload is arbitrary caller text and the only
    stable facts about it are its two delimiters. So the id is PARSED once,
    here, and every rung downstream judges the locator's own text and never the
    parameter set.
    """
    param = ""
    head = ref
    open_at = ref.find("[")
    if open_at != -1 and ref.endswith("]"):
        param = ref[open_at:]
        head = ref[:open_at]
    path_part, separator, rest = head.partition("::")
    chain = [segment.strip() for segment in rest.split("::")] if separator else []
    return path_part.strip(), chain, param




#: Directories a locator's file is never found in and which dominate the walk
#: cost of looking for it. Pruned by name at every level (D-131).
_TEST_SEARCH_PRUNE = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "__pycache__",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build",
    ".tox", ".nox", "site-packages", ".worktrees",
})




def _resolve_test_path(path_part: str, project_root: str) -> Path | None:
    """The file a `path::test` locator's path names, wherever it is rooted.

    D-131 — THE LANE'S EXISTENCE RUNG FIRED ONLY FOR ONE SPELLING OF THE ROOT.
    -------------------------------------------------------------------------
    `_regression_test_problem`'s provable half read `Path(project_root) /
    path_part` and, when that did not resolve, returned None — "I could not
    find your file" was treated as "no problem". This run's teammates cite
    mcp-server-relative paths (`tests/test_fix_gate.py`) while `project_root`
    is the repository root, so the rung never fired for anyone. Driven on a
    LATENT defect: `Foundry-Fix(regression_test='tests/test_ghost.py::test_ghost')`
    returned ok True and closed the defect with no such file anywhere in the
    tree, and `tests/test_fix_gate.py::test_totally_absent_name` — a REAL file
    that defines no such test — was accepted for the same reason.

    ST-003's guard is that "the locator names a real test", and
    `agents/teammate.md` Step 7 promises the path must exist and the test must
    be a `def test_`-shaped symbol inside it. Neither survives a rung that only
    fires when the caller happened to root its path the way this process did.

    So the path is resolved against the root it is relative to, in order:

      1. as given, if absolute;
      2. `project_root / path_part`, the spelling that already worked;
      3. the unique file BENEATH `project_root` whose path ends with
         `path_part` — which is how an mcp-server-relative or
         plugin-relative locator resolves. Ambiguity is not resolution: two
         files ending the same way mean the locator does not single one out,
         and the caller is told so rather than having one guessed for it.

    LEAD RULING, GRIND cycle 7: this REPLACES the D-065 note that the
    filesystem check "remains an addition, never a precondition". That note
    reasoned from a server running against a TARGET repo whose tests it cannot
    resolve; step 3 resolves exactly that case, and the disagreement is
    recorded in the run's concerns.md.

    Returns None when the path resolves nowhere — a distinct answer from "it
    resolved and the test is missing", which is why the caller branches on both.
    """
    import os

    candidate = Path(path_part)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None

    direct = Path(project_root) / path_part
    if direct.is_file():
        return direct

    # Step 3. Walked rather than globbed so the heavy directories are pruned
    # before they are descended into; a repo-wide `rglob` over `.git` and
    # `node_modules` is seconds this door does not have.
    suffix = "/" + path_part.strip("/")
    basename = os.path.basename(path_part.rstrip("/"))
    if not basename:
        return None
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(project_root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _TEST_SEARCH_PRUNE and not d.startswith(".worktree")
        ]
        if basename not in filenames:
            continue
        hit = Path(dirpath) / basename
        if hit.as_posix().endswith(suffix):
            found.append(hit)
            if len(found) > 1:
                # Ambiguous: two roots both answer, so the locator singles out
                # neither. Treated as unresolved rather than as a coin flip.
                return None
    return found[0] if found else None




#: How rung 2 of `_regression_test_problem` SPELLS the discovery rule it just
#: applied, DERIVED from the same constants `vocab.is_test_file` reads rather
#: than re-typed beside it (FR-034).
#:
#: D-222 is why the derivation exists. The recogniser once accepted `*_test.py`
#: and any path segment spelled `tests`, no `pyproject.toml` in this repo asked
#: for either, and casting 1 narrowed it to the configured globs. This refusal's
#: message still read "(test_*.py, *_test.py, conftest.py, or under a tests/
#: directory)" — naming two shapes the rung it sits under now REFUSES, so a
#: caller who followed the message got refused again for doing exactly what it
#: said. A literal beside a predicate is a copy free to drift from it, which is
#: the same shape as the enum drift `server.py`'s header block describes.
#:
#: D-231 / the cycle-27 lead ruling is why `PYTEST_TESTPATHS` is not in it.
#: `testpaths` SEEDS argument-less collection and filters nothing — `pytest
#: --collect-only .` against this configuration collects a root-level
#: `test_*.py` — so `is_test_file` classifies on the BASENAME alone, in any
#: directory. The phrase kept a "under tests/" clause the predicate below it had
#: stopped enforcing, which is this constant's own failure mode arriving from
#: the other side: the message narrower than the rung rather than wider.
_PYTEST_DISCOVERY_PHRASE = (
    ", ".join(PYTEST_PYTHON_FILES)
    + f" or {PYTEST_CONFTEST_BASENAME}"
    + ", in any directory"
)




def _regression_test_problem(ref: str, project_root: str) -> str | None:
    """The named reason a `regression_test` locator is unusable, else None.

    CT-004 is precise about the size of this gate: "within the lane, refusal
    only when the locator is absent or DOES NOT NAME A TEST". It is NOT the
    adjacent-path ladder — no linkage to a statement, no ten-minute apparatus —
    because a LATENT fix has no adjacent-path statement to link against and
    ST-003 says none is demanded. What it is, and must be, is a check that the
    thing named is a test.

    D-065 — THE ONE FIELD THE LANE RESTS ON VALIDATED NOTHING.
    ---------------------------------------------------------
    This ladder's only "is it a test" rung was `_ref_names_a_test_function`,
    whose body is `_TEST_REF_EXTENSION.search(leaf) is None` — "the leaf has no
    file extension" and nothing more (it is now named
    `_ref_singles_out_a_leaf` for that reason). Driven against the real handler
    on a LATENT defect, all of these were ACCEPTED and closed the defect:

        src/auth/session.py::refresh_session   <- the defect's OWN production
                                                  symbol in its OWN file
        src/auth/session.py::helper
        src/nonexistent.py::whatever
        a::b
        the fix::works now

    Worse than accepted: `_REGRESSION_DEF_TEMPLATES` resolved
    `src/auth/session.py`, found `def refresh_session`, and actively CONFIRMED
    the broken production function as the regression test holding its own fix.

    The sibling LIVE-lane checker in this module already had the missing rungs
    and this function called neither. Three are added, in the order a caller
    most needs to hear them:

      1. prose — a locator with whitespace in it is a note, not a reference
         (`_test_ref_problem`'s first rung, same wording shape);
      2. the PATH must satisfy vocab's `is_test_file`, which is the repo's own
         pytest-discovery predicate (FR-034). A production module is not a
         place a regression test can live, however the leaf is spelled;
      3. the NAME must read as a test (`_TEST_REF_NAMES_A_TEST`, the same
         `test|spec` pattern the LIVE ladder applies) and must not be the
         defect's own symbol — a fix cannot be held by the function it fixed.

    D-131 — AND THE FILESYSTEM CHECK IS NOW A PRECONDITION, BECAUSE IT CAN BE.
    -------------------------------------------------------------------------
    This block used to read "THE FILESYSTEM CHECK REMAINS AN ADDITION, NEVER A
    PRECONDITION", on the reasoning that a server running against a TARGET repo
    frequently cannot resolve its tests and "I could not find your file" is
    unfalsifiable from where the caller stands. The consequence was that the
    one field the lane rests on validated nothing whenever the caller rooted
    its path differently from this process: `tests/test_ghost.py::test_ghost`
    closed a LATENT defect with no such file in the tree.

    `_resolve_test_path` removes the premise — it resolves the locator against
    the root it is relative to, including by unique suffix beneath
    `project_root` — so an unresolvable path now means the file is not there,
    and ST-003's "the locator names a real test" is enforceable. LEAD RULING,
    GRIND cycle 7; the superseded note is recorded in the run's concerns.md.

    Rungs 2 and 3 stay LEXICAL and stay ahead of the filesystem, so a caller
    pointing into production code is told THAT rather than "file not found".

    D-145 — AND EVERY RUNG JUDGES THE LOCATOR, NEVER THE PARAMETER SET.
    ------------------------------------------------------------------
    The id is split ONCE by `_split_pytest_node_id` (read its block for the
    three-cycle history that makes the parser the deliverable), and the two
    rungs that used to read the whole string — the prose rung and the
    single-leaf rung — now read `locator`, the id with its parameter id
    removed. A bracket payload is arbitrary caller text that pytest keeps
    verbatim, so a `::` or a space inside it says nothing whatever about
    whether the caller wrote a locator or a note.
    """
    ref = (ref or "").strip()
    if not ref:
        return "absent — a LATENT fix closes on a named regression test"
    path_part, chain, param_id = _split_pytest_node_id(ref)
    # The caller's locator WITHOUT the parameter set — what the rungs below
    # judge. The refusals still quote `ref`, so a caller reads back the id they
    # actually wrote (D-145).
    locator = ref[: len(ref) - len(param_id)] if param_id else ref
    if any(ch.isspace() for ch in locator):
        return (
            f"{ref!r} is prose, not a locator. Give a reference such as "
            "tests/test_report.py::test_absent_section_is_named"
        )
    if "::" not in locator:
        return (
            f"{ref!r} is not a locator of the form path::test — a LATENT fix "
            "closes on a locator such as "
            "tests/test_report.py::test_absent_section_is_named, not on prose"
        )
    path_part = _normalize_path(path_part)
    # The TEST's own name is the last element of the name chain, so a method on
    # a `Test` class (`path::TestSweeper::test_evicts`) resolves to the method
    # pytest runs rather than to the class holding it.
    name = chain[-1] if chain else ""
    if not path_part or not name:
        return f"{ref!r} has an empty path or test name on one side of '::'"
    if not _ref_singles_out_a_leaf(locator):
        return (
            f"{ref!r} names a FILE, not a test inside one — point at the test "
            "that would fail if this defect came back"
        )
    # Rung 2 (D-065): the path is a test file by the repo's own discovery rule.
    if not is_test_file(path_part):
        return (
            f"{path_part!r} is not a test file — {ref!r} points into production "
            f"code. A regression test lives where pytest collects it "
            f"({_PYTEST_DISCOVERY_PHRASE}); name the test that would fail if "
            "this gap came back"
        )
    # Rung 3 (D-065): the name reads as a test, and is not the defect's own.
    if not _TEST_REF_NAMES_A_TEST.search(name):
        return (
            f"{name!r} does not name a test — {ref!r} points at some other "
            "symbol in the file. Name the test function or Test class that "
            "would fail if this gap came back"
        )
    if name.casefold() in _PLACEHOLDER_NAMES:
        return f"{name!r} is a placeholder, not the name of a test"

    # NO OWN-SYMBOL RUNG HERE, DELIBERATELY, and this is where the two ladders
    # legitimately differ. `_test_ref_problem` refuses a reference naming the
    # defect's own symbol because AC-013 asks the LIVE lane for a test driving
    # an ADJACENT path. ST-003 asks the LATENT lane for "the test that would
    # fail if this gap came back", which is a test OF the defect's own symbol —
    # `tests/test_session.py::test_refresh_session` for a gap in
    # `refresh_session` is the right answer, not the wrong one. The own-file
    # rung would be worse still: a defect filed against a test file has its
    # regression test in that same file. What D-065 needs is already complete
    # above — a production path is never a test file, so the defect's own
    # production symbol is refused by rung 2 naming exactly that reason.

    # The provable half (D-131). An unresolvable path is now a REFUSAL: the
    # resolver already tried the locator as given, relative to project_root, and
    # by unique suffix beneath it, so "nowhere" means the file is not there.
    candidate = _resolve_test_path(path_part, project_root)
    if candidate is None:
        return (
            f"{path_part!r} does not resolve to a file — searched it as given, "
            f"under the project root, and by unique path suffix beneath it. A "
            "LATENT fix closes on a test that EXISTS: commit the test first, "
            "then name it here (a locator for a file that is not in the tree "
            "names no test that could fail if this gap came back)"
        )
    text, problem = read_text_file(candidate)
    if problem is not None:
        # Resolved but unreadable — nothing was compared, and that is not the
        # same answer as "the test is missing". The house rule for a read this
        # door does not own: degrade to the lexical rungs, which already held.
        return None
    if any(
        template.format(name=name) in text for template in _REGRESSION_DEF_TEMPLATES
    ):
        return None
    return (
        f"{path_part} exists but defines no {name!r} — the locator names a test "
        "that is not in the file it points at"
    )




# --------------------------------------------------------------------------- #
# The lead lane's measurement (CT-006 / ST-004 / FR-016 / FR-046 / AC-021)
# --------------------------------------------------------------------------- #


#: D-075 — git's rename compaction, which numstat emits in the PATH field.
#: `git show --numstat` (no -z) renders a rename as one field, not two: either
#: the braced form with the common prefix and suffix factored out
#: (``src/{f20.py => f20_renamed.py}``, ``{src => tests}/a.py``) or, when there
#: is nothing in common, the bare ``old => new``. Both are DISPLAY strings; the
#: path they name is the destination.
_NUMSTAT_RENAME_BRACE = re.compile(r"^(.*)\{(.*?) => (.*?)\}(.*)$")


_NUMSTAT_RENAME_BARE = " => "




def _numstat_rename_paths(field: str) -> tuple[str, str]:
    """``(destination, source)`` for one numstat path field; source "" if none.

    D-075: the field was taken as a path verbatim, so a rename recorded the
    git RENDERING — `src/{f20.py => f20_renamed.py}` — as the `file` on the
    audit record GI-003 requires, and `is_test_file('{src => tests}/a.py')`
    answered False, so a commit renaming a source file INTO the tests tree
    escaped the exclusion FR-016 depends on and still consumed the one-file
    lead lane. Both halves come from the same unparsed string, so both are
    fixed by parsing it once, here, and nowhere else.
    """
    match = _NUMSTAT_RENAME_BRACE.match(field)
    if match:
        prefix, old, new, suffix = match.groups()
        def _join(middle: str) -> str:
            return re.sub(r"/{2,}", "/", f"{prefix}{middle}{suffix}")
        return _join(new), _join(old)
    if _NUMSTAT_RENAME_BARE in field:
        old, _, new = field.partition(_NUMSTAT_RENAME_BARE)
        return new.strip(), old.strip()
    return field, ""




#: D-105 — what `git show` may be handed as a revision on the lead lane.
#:
#: A git object name is 7-40 lowercase hex digits, which is what `git rev-parse`
#: prints and what a lead pastes out of a commit. Anchored with `fullmatch` at
#: the call site so nothing can lead with `-` and be read as an option; the lane
#: measures ONE commit, so a range (`A..B`) and a ref name are refused here
#: rather than measured.
_OBJECT_NAME_RE = re.compile(r"[0-9a-f]{7,40}")




def _numstat_measurement(fix_commit: str, project_root: str) -> dict:
    """`git show --numstat` on one commit, reduced to the lane's two numbers.

    Returns ``{"ok": bool, "files": [non-test paths], "lines": int,
    "per_file": [{"file", "renamed_from", "added", "deleted", "lines"}],
    "error": str}``. ``lines`` is added-plus-deleted over the NON-TEST files
    only (FR-016: "test files excluded from the count"), classified by vocab's
    `is_test_file` so the lane and the report agree about what a test is.

    RENAMES ARE PARSED, NOT COPIED (D-075). `_numstat_rename_paths` reduces
    git's display form to the destination path plus the source it came from, so
    the `files` list carries real paths a reader can resolve and `renamed_from`
    keeps what was lost. A rename is classified NON-TEST when EITHER side is a
    non-test path: moving `src/a.py` to `tests/a.py` deletes production code,
    which is exactly the change FR-016's exclusion must not wave through, and
    the symmetric direction (a test promoted into src) is a source change too.

    PER-FILE COUNTS ARE RETURNED (D-073). `lines` is a sum over every non-test
    file, and the caller that records the lead_fix audit row needs to know
    whether that sum belongs to one file or to five — a record naming the FIRST
    file beside the TOTAL count says something false about both.

    Binary files report ``-`` for both counts in numstat; they contribute a file
    to the count and zero lines, which is the honest reading — a binary blob is
    not twenty lines of anything.

    Runs OUTSIDE the ledger transaction, deliberately. It needs only the commit,
    never the record, so holding an flock that every concurrent GRIND fix
    contends on across a subprocess would buy nothing.
    """
    import subprocess

    result = {
        "ok": False, "files": [], "test_files": [], "lines": 0, "per_file": [],
        "error": "",
    }

    # D-105 — THE REVISION IS VALIDATED BEFORE git SEES IT.
    # ----------------------------------------------------
    # This interpolated the caller's `fix_commit` straight into
    # `git show --numstat --format= <value>` with no terminator and no
    # validation, so a LEADING-DASH value was consumed by git as an OPTION and
    # git was left with no revision at all — defaulting to HEAD. Driven on one
    # run, defect D-001, tier LIVE, authored_by=lead: the real 10-file/500-line
    # commit was correctly refused, while `fix_commit='-1'` returned ok=True and
    # wrote a `lead_fix` record naming D-001, file src/tiny.py, line_count 3 and
    # fix_commit '-1' — a measurement of HEAD, a commit the lead never named.
    # Confirmed at the git layer (2.50.1): `git show --numstat --format= -1`
    # prints HEAD's numstat with rc=0, and `--all`, `--quiet`, `--stat` and
    # `HEAD~1..HEAD` behave the same.
    #
    # That is GI-003's named violation verbatim — "a Foundry-Fix that accepts a
    # LIVE lead fix without measuring eligibility" — and it corrupts the audit
    # trail GI-003 exists to create: the server-written handoff, handoffs.md and
    # the F6 lead-fix row all assert a measurement of an object never named.
    #
    # BOTH halves, because either alone is a half-fix. The shape check refuses a
    # non-object-name by NAME, before any subprocess runs; `--end-of-options`
    # then guarantees that whatever passes the check is read as a revision and
    # never as a flag, so a future edit that loosens the pattern cannot
    # re-open the option channel. An object name is hex and lowercase — abbrev
    # or full — which is exactly what `git rev-parse` prints and what a lead
    # pastes; a ref name, a range or a relative spelling is refused here rather
    # than measured, since the lane is a measurement OF ONE COMMIT and a range
    # is not one.
    if not isinstance(fix_commit, str) or not _OBJECT_NAME_RE.fullmatch(fix_commit):
        result["error"] = (
            f"fix_commit is not a git object name: {fix_commit!r}. The lane is "
            "measured with `git show --numstat` on ONE commit, so fix_commit "
            "must be an abbreviated or full commit SHA (7-40 lowercase hex "
            "digits) — not a ref, a range, or an option"
        )
        result["field"] = "fix_commit"
        return result

    try:
        proc = subprocess.run(
            [
                "git", "-C", project_root,
                # D-238 — BEFORE the subcommand, because it is a git-wide config
                # override, and BEFORE anything reads a path out of this output.
                # At its default (true) `core.quotepath` escapes every non-ASCII
                # byte and wraps the path in quotes, and nothing here unescaped
                # it. Driven in a throwaway repo: a commit changing src/a.py (20
                # lines) plus tests/test_café.py and schemas/modèle.py by one
                # line each measured as files ['"schemas/mod\\303\\250le.py"',
                # 'src/a.py', '"tests/test_caf\\303\\251.py"'], test_files [],
                # lines 25, and the lane refused "changes 3 non-test file(s)" —
                # `vocab.is_test_file` cannot see a test file whose derived
                # basename ends `.py"`, so FR-016's exclusion did not apply and
                # a one-file lead fix was refused for two files it never had.
                # The second consequence outlives the refusal: on an ACCEPTED
                # fix the escaped bytes are what `record_lead_fix_handoff`
                # persists as `file`, so handoffs.md, report.json and REPORT.md
                # carry a path no reader can resolve.
                "-c", "core.quotepath=false",
                "show", "--numstat", "--format=",
                # Everything after this is a revision or a path, never a flag.
                "--end-of-options", fix_commit,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError) as exc:
        result["error"] = f"could not run git show on {fix_commit}: {exc}"
        return result
    if proc.returncode != 0:
        result["error"] = (
            f"git show could not read {fix_commit}: "
            f"{proc.stderr.strip()[:160] or 'unknown commit'}"
        )
        return result

    for line in proc.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        added, deleted = parts[0], parts[1]
        # A rename with -z would arrive as two extra fields; without -z git
        # compacts it into the third. Both are handled, so the parse does not
        # depend on which spelling a future caller's flags produce.
        if len(parts) >= 4 and parts[3].strip():
            path, renamed_from = parts[3].strip(), parts[2].strip()
        else:
            path, renamed_from = _numstat_rename_paths(parts[2])
        # D-238: AFTER the rename split, and that order is load-bearing.
        # `core.quotepath=false` leaves a path holding a double quote, a
        # backslash or a control character quoted — git quotes those whatever
        # quotepath says, because the quoting is what keeps the record on one
        # line — and for such a rename git falls back to the BARE `"a" => "b"`
        # form, quoting each side SEPARATELY. Decoding the whole field first
        # would therefore hand `_numstat_rename_paths` a string whose quotes had
        # already been consumed; decoding each side after the split gives both
        # real paths. Verified at git 2.50.1 on renames in both forms.
        path = _decode_git_path(path)
        renamed_from = _decode_git_path(renamed_from) if renamed_from else ""
        # EITHER side non-test makes the entry non-test (D-075). A rename out of
        # the tests tree changes production code; a rename into it removes some.
        is_test = is_test_file(path) and (
            not renamed_from or is_test_file(renamed_from)
        )
        lines = sum(int(c) for c in (added, deleted) if c.isdigit())
        if is_test:
            # D-107: the test rows are COUNTED, not merely skipped. The
            # zero-non-test-file refusal explains itself with "this commit
            # touches only test files", and that sentence is true only when
            # there were test files to touch — see `_lead_lane_problem`.
            result["test_files"].append(path)
            continue
        result["files"].append(path)
        # Keyed `path`, `added`, `deleted`, `renamed_from` — C-8's row shape,
        # which `record_lead_fix_handoff` reads. Spelled the sibling's way
        # rather than this module's, because a second spelling of one record is
        # how the audit row and the lane measurement come to disagree.
        result["per_file"].append({
            "path": path,
            "renamed_from": renamed_from or None,
            "added": int(added) if added.isdigit() else None,
            "deleted": int(deleted) if deleted.isdigit() else None,
            "lines": lines,
        })
        result["lines"] += lines
    result["ok"] = True
    return result




def _lead_lane_problem(fix_commit: str, project_root: str) -> str | None:
    """The named reason a LIVE lead-authored fix exceeds its lane, else None.

    ST-004 / CT-006: exactly `LEAD_LANE_MAX_FILES` non-test file, at most
    `LEAD_LANE_MAX_LINES` added-plus-deleted lines over it. The refusal names
    the COUNT that exceeded the lane (AC-021), because "too big" tells a lead
    nothing about which half to shrink.

    A commit git cannot read is a refusal too. The lane is a MEASUREMENT, and a
    measurement that could not be taken is not a measurement that passed — the
    shape that would let an unbounded lead fix through by naming a SHA that does
    not exist.

    D-020 — EXACTLY ONE, AND THE REFUSAL SAYS WHICH DIRECTION IT MISSED BY.
    ----------------------------------------------------------------------
    LEAD RULING (recorded SPEC_AMBIGUOUS in the run's state.json): the lane
    requires EXACTLY one non-test file for a LIVE lead fix. FR-014 states the
    lane as "LIVE if one file and <= 20 lines"; FR-016's "more than one" names
    one refusal condition, not the only one. So the `!=` comparison is correct
    and stays, and a test-only LIVE fix is outside the lane and goes to a GRIND
    teammate.

    What was wrong is what the refusal SAID. A commit with zero non-test files
    was refused with "changes 0 non-test file(s) ... Dispatch this to a GRIND
    teammate instead" — the too-big message, on a commit that is too small,
    telling a lead to shrink something that is already empty. The two
    directions are now separate branches with separate remedies, because "add
    the source change this fix is missing" and "split this commit up" are
    opposite instructions and a lead acting on the wrong one loses a cycle.
    """
    measured = _numstat_measurement(fix_commit, project_root)
    if not measured["ok"]:
        return (
            f"{measured['error']}. The lane is measured from the commit, so a "
            "commit that cannot be read cannot be measured."
        )
    files = measured["files"]
    if not files:
        # D-107 — THE DIAGNOSIS IS CONDITIONAL ON THE THING IT DIAGNOSES.
        #
        # The "this commit touches only test files" clause was appended
        # UNCONDITIONALLY to every zero-non-test-file refusal, and is false
        # whenever the commit touches nothing at all. Driven with
        # `git commit --allow-empty` on a LIVE lead fix: "…ed2ad97df44d changes
        # 0 non-test file(s) — the lane requires exactly 1, and this commit
        # touches only test files. A LIVE defect whose fix is a test-only change
        # is outside the lane" — on a commit with no files of any kind, and the
        # same false clause fired for any commit git reports zero numstat rows
        # for. The refusal DIRECTION was right and the D-020 ruling's two
        # mandates held (the real count and the rule are both stated, and the
        # too-big message is never reused); only the explanation lied, on the
        # one refusal whose wording that ruling explicitly regulated.
        #
        # So the remedy splits with the diagnosis, because "name the commit that
        # carries the source change" and "this commit is empty" send a lead to
        # different places.
        if measured["test_files"]:
            return (
                f"{fix_commit[:12]} changes 0 non-test file(s) — the lane "
                f"requires exactly {LEAD_LANE_MAX_FILES}, and this commit "
                f"touches only test files ({', '.join(measured['test_files'][:5])}). "
                "A LIVE defect whose fix is a test-only change is outside the "
                "lane: name the commit that carries the source change, or "
                "dispatch it to a GRIND teammate."
            )
        return (
            f"{fix_commit[:12]} changes no files at all — the lane requires "
            f"exactly {LEAD_LANE_MAX_FILES} non-test file, and git reports no "
            "changed paths for this commit. Name the commit that actually "
            "carries the fix (an empty or already-merged commit measures "
            "nothing), or dispatch it to a GRIND teammate."
        )
    if len(files) != LEAD_LANE_MAX_FILES:
        return (
            f"{fix_commit[:12]} changes {len(files)} non-test file(s) "
            f"({', '.join(files[:5])}) — the lane requires exactly "
            f"{LEAD_LANE_MAX_FILES}. Dispatch this to a GRIND teammate instead."
        )
    if measured["lines"] > LEAD_LANE_MAX_LINES:
        return (
            f"{fix_commit[:12]} changes {measured['lines']} added-plus-deleted "
            f"line(s) in {files[0]} — the lead lane is at most "
            f"{LEAD_LANE_MAX_LINES}. Dispatch this to a GRIND teammate instead."
        )
    return None




def _check_reported_prompt_hash(fdir: Path, casting_id, reported_hash) -> dict | None:
    """C-8's ``check_reported_prompt_hash`` — None, or the refusal dict."""
    from foundry_mcp.tools.foundry_handoff import check_reported_prompt_hash

    return check_reported_prompt_hash(fdir, casting_id, reported_hash)




def _record_lead_fix_handoff(fdir: Path, **fields) -> dict:
    """C-8's ``record_lead_fix_handoff`` — the server's own audit record."""
    from foundry_mcp.tools.foundry_handoff import record_lead_fix_handoff

    return record_lead_fix_handoff(fdir, **fields)




@ledger_refusals
def foundry_mark_defect_fixed(
    defect_id: str,
    cycle: int,
    adjacent_path_statement: str = "",
    adjacent_path_test: str = "",
    project_root: str = ".",
    authored_by: str = "",
    regression_test: str = "",
    fix_commit: str = "",
    prompt_hash: str | None = None,
    casting_id: int | str | None = None,
) -> dict:
    """Mark a defect as fixed, declaring the fix's blast radius.

    FR-009 / CT-001 / ST-004. This call validated NOTHING before — it matched
    the first id and flipped status, which is how fixes kept opening
    regressions their own tests could not see. Two declarations are now
    preconditions of the transition:

      adjacent_path_statement — who ELSE calls this, what else transitions
          here, what runs concurrently (A-017).
      adjacent_path_test — a reference to a test that drives at least one
          NAMED adjacent path: a different caller, transition, or concurrent
          interaction than the path the defect was found on (A-018 / FR-010).

    A call missing either is refused with a message naming each missing field,
    in the same shape as the acceptance gate's rejections. The declarations are
    persisted on the defect record, so the blast radius a fix claimed to have
    considered is auditable after the fact.

    Both declarations are also checked for CONTENT, not merely presence: the
    statement must not restate the defect's own symbol, and the test reference
    must look like a test reference and must not name only the defect's own
    path (see ``_test_ref_problem``). A presence-only gate accepted "n/a" and
    "tested it manually", which is the gate passing while the guarantee it
    exists for does not hold. Every failing field is named in one refusal.

    The whole read-modify-write runs inside ``ledger_transaction`` (FR-020 /
    AC-025). It used to be an UNLOCKED load / mutate / save while every other
    writer of defects.json held that lock, so a fix landing between a peer's
    read and write was silently discarded by the peer's ``.tmp`` rename — the
    call returned ok and the defect stayed open. Concurrent fixes are the norm
    in GRIND, not an edge case: a whole wave of teammates closes defects in
    parallel against one ledger.

    ``fixed_in_cycle`` is stamped from the SERVER counter (FR-005 / ST-001);
    the caller's ``cycle`` is retained as ``declared_fixed_cycle`` for audit
    only. Escalation reads these numbers back, and it accumulated against
    lead-asserted cycles while the server counter sat at 0.

    CEREMONY IS PROPORTIONAL TO THE EVIDENCE TIER (US-003 / ST-003 / ST-004)
    -----------------------------------------------------------------------
    Every fix used to pay the full LIVE declaration, so a one-line change
    closing a scan-derivation gap nobody had reproduced cost the same apparatus
    as a fix for a driven, reachable failure. The check order is C-13's and it
    is LOCKED, so that two callers making the same mistake are told about the
    same field first:

      1. tool-wide — the defect exists; ``authored_by`` is present and in
         FIX_AUTHORS; a teammate names a matching ``prompt_hash``; a lead names
         a ``fix_commit``, whatever the tier (FR-053).
      2. the tier lane — LATENT closes on a ``regression_test`` locator alone
         (ST-003); LIVE keeps the full adjacent-path declaration, unweakened
         (FR-015 / AC-023). An UNKNOWN-tier record takes the LIVE lane: it is a
         record nobody classified, and the fail-safe direction is the stricter
         ceremony, never the looser one.
      3. the lead lane's measurement — LIVE lead fixes only (FR-046 / CT-006).
      4. persist, then the server's own ``lead_fix`` handoff record (GI-003).

    The failing-then-passing statement is NOT an input here (FR-041 / AC-012).
    It belongs in the teammate's completion report, where PROVE can drive the
    test to confirm it; demanding it as a string on this call would be a claim
    the server cannot check, which is the ceremony this requirement removes.
    """
    from foundry_mcp.tools.foundry import _dict_records, ledger_transaction

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    defects_path = fdir / "defects.json"

    statement = (adjacent_path_statement or "").strip()
    test_ref = (adjacent_path_test or "").strip()
    author = (authored_by or "").strip()
    regression_ref = (regression_test or "").strip()
    commit = (fix_commit or "").strip()
    server_cycle = current_cycle(fdir)

    # --- Tool-wide rungs that need no ledger read, evaluated before the lock --
    #
    # GI-003 / CT-005 / AC-020: `authored_by` is REQUIRED on every fix. A lead
    # fix recorded as free prose in a hand-written handoff is a fix nothing can
    # measure or count, and this field is what makes the difference visible.
    if not author:
        return {
            "error": (
                f"Cannot mark {defect_id} fixed — missing required field(s): "
                "authored_by. Every fix declares who wrote it."
            ),
            "missing_fields": ["authored_by"],
            "hint": (
                "Pass authored_by='teammate' for a fix a GRIND teammate wrote, "
                "or authored_by='lead' for one you wrote yourself in the bounded "
                "lead lane. A lead fix additionally requires fix_commit."
            ),
        }
    if author not in FIX_AUTHORS:
        return {
            "error": (
                f"Cannot mark {defect_id} fixed — unknown authored_by: "
                f"{author!r}. Must be one of: {', '.join(sorted(FIX_AUTHORS))}."
            ),
            "missing_fields": ["authored_by"],
            "invalid_fields": [{
                "field": "authored_by",
                "reason": f"{author!r} is not a member of FIX_AUTHORS",
            }],
            "hint": "Who wrote the fix is a closed vocabulary: lead or teammate.",
        }

    # AC-030 / CT-011 — POINTER DISPATCH'S OTHER HALF.
    #
    # Foundry-Spawn-Teammate hands a teammate a PATH and a HASH instead of the
    # prompt text; only an agent that actually read the file can state the hash
    # back. That is advice until a gate consumes it, and this is one of the two
    # gates that does (Foundry-Accept-Casting is the other). Both call the SAME
    # `check_reported_prompt_hash`, because a hash rung that exists at one door
    # and not the other lets an unread prompt through whichever door the lead
    # happens to walk.
    if author == "teammate":
        hash_missing = []
        if not (prompt_hash or "").strip():
            hash_missing.append("prompt_hash")
        if casting_id is None or str(casting_id).strip() == "":
            hash_missing.append("casting_id")
        if hash_missing:
            return {
                "error": (
                    f"Cannot mark {defect_id} fixed — missing required field(s): "
                    f"{', '.join(hash_missing)}. A teammate-authored fix states "
                    "back the hash of the prompt it was dispatched with."
                ),
                "missing_fields": hash_missing,
                "hint": (
                    "The teammate's completion report carries the sha256 it read; "
                    "pass it as prompt_hash together with the casting_id it was "
                    "dispatched for. Only reading the prompt file produces the "
                    "right answer, which is the point."
                ),
            }
        hash_refusal = _check_reported_prompt_hash(fdir, casting_id, prompt_hash)
        if hash_refusal is not None:
            return hash_refusal

    # FR-053 / CT-005 / AC-020: a lead fix always names its commit, whatever the
    # tier. The commit is what the `lead_fix` handoff record carries.
    #
    # D-194's sibling in this file, restated to the ruling's TWO HALVES. This
    # said the audit trail exists "even on a LATENT fix that is never
    # measured", and that is not what the door below does:
    #
    #   1. THE COMMIT IS MEASURED ON BOTH TIERS. `_numstat_measurement` runs on
    #      the LATENT arm too — that is why an unresolvable fix_commit is
    #      refused there (D-132) and why a LATENT `lead_fix` record carries a
    #      real per-file measurement rather than nulls (D-046 / D-073).
    #   2. THE LANE LIMIT REFUSES ONLY WHEN LIVE. `_lead_lane_problem`'s file
    #      and line bounds are what FR-046 / CT-006 / ST-004 scope to LIVE:
    #      "measures fix_commit only when the defect is LIVE" is about whether
    #      the lane REFUSES, not about whether the numbers are taken.
    #
    # "Never measured" was the retired spelling for "no limit applies", and a
    # comment that keeps it tells the next author the LATENT arm takes no git
    # read at all — which is precisely the reading D-132 was filed against.
    if author == "lead" and not commit:
        return {
            "error": (
                f"Cannot mark {defect_id} fixed — missing required field(s): "
                "fix_commit. Every lead-authored fix names the commit it landed "
                "in, whatever the defect's tier."
            ),
            "missing_fields": ["fix_commit"],
            "hint": (
                "Commit the fix first, then pass its SHA as fix_commit. The "
                "server writes a lead_fix handoff record carrying it, and on a "
                "LIVE defect it measures the commit against the lead lane."
            ),
        }

    # Every branch runs INSIDE the transaction: the not-found and adjacency
    # checks both read the target record, so evaluating them outside would
    # re-open the same read-then-write window this lock exists to close. A
    # refusal mutates nothing, and writing back an unmodified document is a
    # no-op.
    refusal: dict | None = None
    open_count = 0
    with ledger_transaction(defects_path, "defects") as records:
        target = None
        # D-128: this door's inline `isinstance(d, dict)` was CORRECT and was
        # still an instance of the class — a hand-applied copy of the filter
        # `_dict_records` exists to hold once. The batch door's identical scan
        # had no copy at all, which is how one door tolerated a malformed
        # record while the other lost the caller's filing to an AttributeError.
        for d in _dict_records(records):
            if d.get("id") == defect_id:
                target = d
                break

        # ST-003 / ST-004 — WHICH LANE THIS FIX IS IN.
        #
        # Read through vocab's `defect_tier`, the one total resolution of the
        # tier a record READS as. A missing key, a null and a string outside
        # DEFECT_TIERS all land on TIER_UNKNOWN, and unknown takes the LIVE
        # lane: a record nobody classified gets the stricter ceremony, never
        # the looser one. Reading it as LATENT would let a fix close on one
        # locator because an older archive never carried the field.
        tier = defect_tier(target) if target else TIER_UNKNOWN
        latent_lane = tier == "LATENT"

        missing = []
        if latent_lane:
            if not regression_ref:
                missing.append("regression_test")
        else:
            if not statement:
                missing.append("adjacent_path_statement")
            if not test_ref:
                missing.append("adjacent_path_test")
        own_symbol = (target.get("symbol") or "").strip() if target else ""
        own_file = (target.get("file") or "").strip() if target else ""

        # Present-but-inadequate declarations, gathered so ONE refusal names
        # every field that failed (CT-001's "naming each missing field" applies
        # to a junk declaration exactly as it does to an absent one — a caller
        # who supplied two non-answers should be told about both, not sent back
        # twice).
        invalid: list[dict] = []
        statement_is_usable = False
        if latent_lane:
            # CT-004: within the lane, refusal ONLY when the locator is absent
            # or does not name a test. No adjacent-path statement, no
            # adjacent-path test, no failing-then-passing prose — ST-003 says
            # none of it is demanded, and demanding it anyway is the ceremony
            # this requirement exists to remove.
            if regression_ref:
                problem = _regression_test_problem(regression_ref, project_root)
                if problem is not None:
                    invalid.append({"field": "regression_test", "reason": problem})
        elif statement:
            # ST-004 / AC-013: the declared path must be ADJACENT — distinct
            # from the path the defect itself was found on — and it must
            # actually be a declaration. The check here was exact equality
            # against the defect's own symbol and nothing else, so 'x', 'none',
            # 'no adjacent paths' and 'the same path' all closed defects
            # (D-050). See `_statement_problem` for the ladder.
            problem = _statement_problem(statement, own_symbol, own_file)
            if problem is not None:
                invalid.append({
                    "field": "adjacent_path_statement",
                    "reason": problem,
                })
            else:
                statement_is_usable = True
        if test_ref and not latent_lane:
            # D-092: the two declarations are a matched PAIR — the statement
            # names the adjacent paths, the reference drives one of them — so
            # the statement is an INPUT to judging the reference, not a
            # separate verdict beside it. It is withheld when it failed its own
            # ladder: a caller told "your reference drives none of the paths
            # your statement named" about a statement that named none would be
            # sent after the wrong problem.
            problem = _test_ref_problem(
                test_ref,
                own_symbol,
                own_file,
                statement=statement if statement_is_usable else "",
            )
            if problem is not None:
                invalid.append({"field": "adjacent_path_test", "reason": problem})

        # CT-006 / FR-046 — MEASURED ONLY WHEN LIVE AND ONLY WHEN LEAD.
        #
        # The lane is a bound on how much a lead may change without dispatching
        # a teammate, and it is measured from the commit rather than claimed in
        # prose. On a LATENT defect the required fix_commit is RECORDED and not
        # measured (CT-006's second clause): a LATENT gap can be a large,
        # mechanical, entirely safe change, and there is no reachable failure
        # whose blast radius the bound is protecting.
        # D-132 — "NOT MEASURED" IS NOT "NOT READ".
        #
        # CT-006's second clause is that on a LATENT defect the required
        # fix_commit "is RECORDED and not measured", and this branch read that
        # as "no git call at all". Driven on a LATENT defect with
        # authored_by=lead and a valid regression_test locator: fix_commit
        # 'not-a-commit', '-1', 'tbd' and forty zeros each returned ok True;
        # the record persisted fix_commit 'tbd'; handoffs.jsonl gained
        # {fix_commit 'tbd', file null, line_count null, files null}; and
        # report.json rendered the row "measurement unavailable - git could not
        # read the commit". `_numstat_measurement` refuses the shape by name
        # (the D-105 rung) and the LATENT path discarded that refusal at the
        # record step (`if not measured["ok"]: measured = None`).
        #
        # FR-053 is verbatim "authored_by=lead always needs fix_commit SO THE
        # lead_fix HANDOFF CARRIES THE COMMIT" — the field exists to make the
        # audit row point at a real object, and a string that names no object
        # carries nothing. CT-005 refuses "when fix_commit is absent on a lead
        # fix"; a value git cannot resolve is absent in every sense that
        # matters to the reader of the record.
        #
        # WHAT STAYS LIVE-ONLY IS THE LANE, exactly as FR-046 / CT-006 / ST-004
        # say: the file count and the line count are limits on how much a lead
        # may change without dispatching a teammate, and a LATENT gap can be a
        # large, mechanical, entirely safe change with no reachable failure
        # whose blast radius the bound protects. So the LATENT arm runs the
        # READ and refuses only on "this is not a commit"; it never compares a
        # count. `_lead_lane_problem` is the LIVE arm and is unchanged — it
        # already runs the same read as its first rung, so neither arm
        # re-implements the other.
        lane_problem = None
        if author == "lead" and target is not None:
            if not latent_lane:
                lane_problem = _lead_lane_problem(commit, project_root)
            else:
                unreadable = _numstat_measurement(commit, project_root)
                if not unreadable["ok"]:
                    lane_problem = (
                        f"{unreadable['error']}. A LATENT lead fix is not "
                        "MEASURED — no file or line limit applies to it — but "
                        "fix_commit is still the commit the lead_fix handoff "
                        "record carries (FR-053), so it must name a real "
                        "object."
                    )

        if target is None:
            refusal = {"error": f"Defect {defect_id} not found"}
        elif missing:
            refusal = {
                "error": (
                    f"Cannot mark {defect_id} fixed — missing required field(s): "
                    f"{', '.join(missing)}. "
                    + (
                        "A LATENT defect closes on a regression_test locator of "
                        "the form path::test — the test that would fail if this "
                        "gap came back."
                        if latent_lane
                        else "adjacent_path_statement must name who else calls "
                        "this, what else transitions here, or what runs "
                        "concurrently. adjacent_path_test must reference a test "
                        "that drives at least one of those adjacent paths."
                    )
                ),
                "missing_fields": missing,
                "tier": tier,
                "hint": (
                    "Write the regression test first, then re-call Foundry-Fix "
                    "with its locator. The failing-then-passing statement goes "
                    "in your completion report, not on this call."
                    if latent_lane
                    else "Write the adjacent-path test first, then re-call "
                    "Foundry-Fix with both declarations. A fix whose blast "
                    "radius is undeclared is how a defect closes and a "
                    "regression opens in the same cycle."
                ),
            }
        elif invalid:
            fields = [item["field"] for item in invalid]
            refusal = {
                "error": (
                    f"Cannot mark {defect_id} fixed — unusable declaration(s): "
                    + "; ".join(f"{item['field']} — {item['reason']}" for item in invalid)
                    + "."
                ),
                # Same key the absent-field refusal uses, so a caller has one
                # place to read "which fields must I supply or repair".
                "missing_fields": fields,
                "invalid_fields": invalid,
                "tier": tier,
                "hint": (
                    "Point at the test that would fail if this gap came back — a "
                    "locator such as tests/test_report.py::test_absent_section_"
                    "is_named, not a note to yourself."
                    if latent_lane
                    else "Name a second path that touches this code, then "
                    "reference the test that drives THAT path — a locator such "
                    "as tests/test_auth.py::test_sweeper_evicts_stale_sessions, "
                    "not a note to yourself."
                ),
            }
        elif lane_problem is not None:
            refusal = {
                "error": (
                    f"Cannot mark {defect_id} fixed by the lead — {lane_problem}"
                ),
                "missing_fields": ["fix_commit"],
                "invalid_fields": [{"field": "fix_commit", "reason": lane_problem}],
                "tier": tier,
                # D-132: the hint follows the arm that fired. On the LATENT
                # arm no limit was compared, so quoting the lane's file and
                # line ceilings there would send the lead to shrink a commit
                # nothing measured.
                "hint": (
                    (
                        "A LATENT lead fix is not measured — no file or line "
                        "limit applies to it — but fix_commit is the commit "
                        "the lead_fix handoff record carries, so it must be a "
                        "commit git can read: paste the SHA "
                        "`git rev-parse --short HEAD` printed for the fix."
                    )
                    if latent_lane
                    else (
                        "The lead lane exists so a cycle whose open defects are "
                        f"all small closes without a teammate spawn: "
                        f"{LEAD_LANE_MAX_FILES} non-test file, at most "
                        f"{LEAD_LANE_MAX_LINES} added-plus-deleted lines. "
                        "Anything larger is a GRIND task."
                    )
                ),
            }
        else:
            target["status"] = "fixed"
            target["fixed_in_cycle"] = server_cycle
            target["declared_fixed_cycle"] = cycle
            target["authored_by"] = author
            if latent_lane:
                target["regression_test"] = regression_ref
            else:
                target["adjacent_path_statement"] = statement
                target["adjacent_path_test"] = test_ref
                if regression_ref:
                    # Never demanded on the LIVE lane, always kept when offered:
                    # a LIVE fix that also names a regression test has said
                    # something true and the record should carry it.
                    target["regression_test"] = regression_ref
            if commit:
                target["fix_commit"] = commit

        open_count = sum(
            1 for d in _dict_records(records) if d.get("status") == "open"
        )

    if refusal is not None:
        return refusal

    # D-035: a fix that lands mid-INSPECT invalidates the width this cycle
    # already decided and swept at. Stamped after the ledger commits, so a
    # rolled-back transaction leaves no claim that a fix landed.
    _note_fix_after_inspect_decision(fdir, defect_id)

    # GI-003 / AC-022 — THE SERVER WRITES THE HANDOFF, NOT THE LEAD.
    #
    # "A lead fix recorded only as free prose in a hand-written handoff" is the
    # named violation, so the record is a side effect of the accepted call and
    # cannot be forgotten. It is written AFTER the ledger commits, because a
    # handoff naming a fix the transaction then rolled back would be the same
    # gap pointing the other way.
    #
    # D-046 — MEASURING IS NOT RECORDING, AND THE LANE TEST IS THE ONLY THING
    # THAT IS LIVE-ONLY.
    # ----------------------------------------------------------------------
    # This branch read `None if latent_lane else ...`, so a LATENT lead fix
    # emitted `{'file': None, 'line_count': None}` while the LIVE fix beside it
    # in the same run emitted the real file and count. GI-003 states the record
    # verbatim as "carrying the defect id, tier, file, line count and test",
    # AC-022 and OT-010 repeat that field list, and not one of the three carries
    # a tier carve-out. What IS LIVE-only is FR-046 / CT-006 / ST-004's
    # ELIGIBILITY test — "measures fix_commit only when the defect is LIVE" is
    # about whether the lane refuses, not about whether the numbers are written
    # down. `_numstat_measurement` is a pure read of the commit and
    # `_lead_lane_problem` is the separate limit check, so the two were already
    # separable and only this call site conflated them.
    #
    # Consequence of the conflation: AC-022's "the generated report lists it"
    # rendered blank file and line columns for every LATENT lead fix, and a
    # report reader could not tell a deliberately unmeasured fix from a
    # measurement nobody took.
    #
    # A commit git cannot read still records None, because there the
    # measurement genuinely could not be taken. D-132 made that unreachable on
    # BOTH lanes rather than only on the LIVE one: `_lead_lane_problem` refuses
    # an unreadable commit on LIVE and the LATENT arm beside it now runs the
    # same read and refuses on the shape, so by the time this executes the
    # commit has resolved. The None branch is kept as the honest answer for a
    # repository state that changes under the call, not as a live path.
    #
    # D-073 — THE ROWS ARE THE RECORD; `file` IS A CONVENIENCE THAT MUST NOT
    # LIE.
    # ----------------------------------------------------------------------
    # This passed `file=measured["files"][0]` beside `line_count=measured["lines"]`,
    # where `lines` is the sum over EVERY non-test file. On the LATENT lane,
    # where any number of files is legal, a 5-file 100-line-each commit was
    # therefore recorded as `{"file": "pkg/m0.py", "line_count": 500}` and
    # REPORT.md rendered `| D-001 | LATENT | pkg/m0.py | 500 |`. A reader
    # concludes 500 lines changed in pkg/m0.py; 100 did, and nothing in the
    # record said four other files were touched. GI-003 / AC-022 / OT-010 make
    # this the audit trail for a fix nobody else reviewed, and a field that is
    # wrong is worse than one that is absent.
    #
    # `files=` (C-8, widened by D-074) is now passed instead: the measurement
    # itself, one row per non-test file. The writer derives `file` — the single
    # path when there is exactly one, None otherwise — and `line_count` from
    # those same rows, so the two cannot disagree and no caller re-derives
    # either.
    # D-170 — `test` CARRIES THE TEST THE LANE MANDATED, NOT WHICHEVER ONE THE
    # CALLER ALSO SENT.
    # ----------------------------------------------------------------------
    # This read `test=regression_ref or test_ref`, so on the LIVE lane an
    # OPTIONAL `regression_test` displaced the MANDATED `adjacent_path_test`.
    # Driven at the door with authored_by=lead on a LIVE defect carrying the
    # full declaration plus an extra regression locator: the record's `test`
    # read `tests/test_lane.py::test_lane_holds` and the adjacent-path test
    # appeared nowhere in the record or in the handoffs.md mirror row.
    #
    # GI-003 / AC-022 make this the audit trail for a fix nobody else reviewed,
    # and the test is one of the five things a reader must be able to
    # re-derive. Which test HOLDS the fix is a property of the lane, not of what
    # the caller volunteered: on LIVE it is the adjacent-path test (AC-023 /
    # FR-015 demand it and nothing else), on LATENT it is the regression test
    # (ST-003 / CT-004 demand that one and no adjacent-path fields at all). So
    # the lane selects, exactly as the persist step above and the forge-log
    # write below already select on `latent_lane`.
    #
    # The optional locator is not dropped — the comment at the persist step
    # says a LIVE fix that also names a regression test "has said something
    # true and the record should carry it", and now it carries it IN ADDITION
    # rather than INSTEAD, under its own name. `regression_test` is casting 2's
    # keyword-only parameter on `record_lead_fix_handoff`; None when the lane
    # already reports it as `test`, so no record states the same locator twice.
    lead_fix_record = None
    if author == "lead":
        measured = _numstat_measurement(commit, project_root)
        if not measured["ok"]:
            measured = None
        lead_fix_record = _record_lead_fix_handoff(
            fdir,
            defect_id=defect_id,
            tier=tier,
            files=(measured["per_file"] if measured else None),
            test=regression_ref if latent_lane else test_ref,
            regression_test=(None if latent_lane else (regression_ref or None)),
            fix_commit=commit,
        )

    forge_log = fdir / "forge-log.md"
    if forge_log.exists():
        with open(forge_log, "a", encoding="utf-8") as f:
            f.write(
                f"\n**{defect_id} FIXED** ({tier}, by {author}) in cycle "
                f"{server_cycle} ({now_iso()})\n"
            )
            if latent_lane:
                f.write(f"- **Regression test:** {regression_ref}\n")
            else:
                f.write(f"- **Adjacent paths:** {statement}\n")
                f.write(f"- **Adjacent-path test:** {test_ref}\n")
            if commit:
                f.write(f"- **Fix commit:** {commit}\n")
            f.write("\n")

    result = {
        "ok": True,
        "defect_id": defect_id,
        "tier": tier,
        "authored_by": author,
        "fixed_in_cycle": server_cycle,
        "declared_cycle": cycle,
        "adjacent_path_statement": statement,
        "adjacent_path_test": test_ref,
        "regression_test": regression_ref,
        "fix_commit": commit,
        "remaining_open": open_count,
    }
    if lead_fix_record is not None:
        result["lead_fix"] = lead_fix_record
    return result




# --------------------------------------------------------------------------- #
# Cross-casting seam: the two helpers tools/foundry.py owns.
#
# Both are imported LAZILY and deliberately unguarded. Importing them at module
# top would make `import foundry_mcp.server` fail outright while the sibling
# casting that owns tools/foundry.py is still in flight, taking every other
# tool in this server down with it; swallowing the ImportError would instead
# hide a real wiring break behind a silent fallback. A lazy import fails loudly
# at exactly the one call site that needs the symbol, naming it.
#
# There must be ONE writer for each of these. Do not add a local ledger writer
# or a local ID mint here \u2014 that duplication is what FR-013 and FR-020 exist to
# remove.
# --------------------------------------------------------------------------- #


def _mint_defect_id(records: list) -> str:
    """Allocate a defect ID that is unique under concurrent filing (FR-020).

    Replaces the positional ``D-{len(defects) + 1:03d}`` mint, which hands the
    SAME id to two streams that read the same ledger snapshot, re-issues a live
    id whenever a record is removed, and wraps silently past D-999 \u2014 the AC-025
    race. ``allocate_record_id`` takes the highest existing suffix instead, and
    its uniqueness comes from the surrounding ``ledger_transaction`` lock, which
    is why every call below sits inside one.
    """
    from foundry_mcp.tools.foundry import allocate_record_id

    return allocate_record_id(records, prefix="D")




def new_defect_record(
    *,
    cycle: int,
    declared_cycle: int,
    source: str,
    defect_type: str,
    tier: str,
    defect_class: str,
    description: str,
    reproduction_attempted: str | None = "",
    spec_ref: str = "",
    symbol: str = "",
    file_path: str = "",
    target_kind: str = "",
    record_id: str = "",
    created_at: str | None = None,
    fallout_of: str | None = None,
    supersedes: str | None = None,
) -> dict:
    """The persisted defect record — ONE shape definition, BOTH filing doors.

    C-2 / CT-001 / CT-002 / US-002. Returns the dict a filing door appends to
    ``defects.json``. The caller supplies the id — ``foundry_add_defect``
    assigns it inside its own transaction, ``foundry_sync_defects`` mints it as
    it builds — and every other field is derived here, once.

    WHY THIS IS A FUNCTION (D-192)
    ------------------------------
    The record was TWO hand-typed literals: one in ``foundry_add_defect``
    (tools/foundry.py), one in the write loop of ``foundry_sync_defects``
    below. The batch door's literal carried a comment asserting that "the batch
    door writes the SAME record shape as the single door, field for field", and
    that claim was false. Driven at HEAD 3584f55 with one finding carrying
    ``target_kind='code'`` through both doors: ``Foundry-Defect`` persisted
    D-001 with ``target_kind='code'``; ``Foundry-Sync`` persisted D-002 with no
    ``target_kind`` key at all. Enforcement was unaffected — both doors refuse
    comment prose identically across LINE_DRIFT_CITE, PROSE_COUNT,
    DIRECTION_WORD and ENUMERATION, and the LATENT security denylist fires at
    both — but the durable record was not: a Sync-filed defect carried no
    evidence that the declaration had ever been made, so it could not be
    audited for it while the same finding filed one door over could.

    That is D-119 (the two doors disagreeing about which cycle a record
    belonged to) and D-077 (the re-tier rule implemented at one door only) a
    third field along, and the answer is the one those two took: the rule is
    DERIVED ONCE and both doors call it. A COMMENT asserting that two literals
    agree was the alternative, and it is what stood here — it is not a
    mechanism, it goes stale the moment either literal gains a field, and
    nothing fails when it does.

    ``target_kind`` is written CONDITIONALLY, exactly as the single door writes
    it: absence stays absence. ``vocab.is_non_comment`` reads ``bool(kind)``, so
    ``""`` and absent answer alike today — but the guarantee this function
    exists to hold is field-for-field agreement with the SINGLE door's shipped
    output, not with a tidier shape, and a record that grows a key it never
    carried is a change to every pre-change archive read.

    WHERE THIS BELONGS (concerns.md, GRIND cycle 15)
    -----------------------------------------------
    The natural site is ``tools/foundry.py``, beside ``validate_defect_filing``
    and ``retier_matching_untiered`` — the two other rules both doors share —
    where neither door needs a new import edge. That file belongs to casting 2
    and this casting may not write it, so the definition lives here, the batch
    door calls it, and ``foundry_add_defect`` keeps its literal until casting 2
    imports this name. Until then the agreement is held by
    ``tests/test_orchestrator_gates.py`` — see
    ``test_both_filing_doors_persist_one_record_shape_over_the_wire``, which
    files ONE finding through both doors over MCP and refuses any difference
    outside ``id`` and ``created_at``.
    """
    record = {
        "id": record_id,
        # ST-001: the server's counter is the authority, full stop.
        "cycle": cycle,
        # D-119: the caller's asserted cycle is persisted beside the server's,
        # never instead of it, so a divergence is visible to migrate and
        # escalation tooling instead of silent.
        "declared_cycle": declared_cycle,
        "source": source,
        "type": defect_type,
        # CT-001 / FR-004: the evidence axis, on every record. Written from the
        # validated value, so a persisted record's tier is always a member of
        # DEFECT_TIERS — vocab.TIER_UNKNOWN is a READ-side sentinel for records
        # written before this release and is NEVER written by a door.
        "tier": tier,
        # CT-002 / FR-007: unconditional, because the validator has already
        # refused an absent or blank class. A record without the key can no
        # longer be produced, so escalation never has to handle one.
        "class": defect_class,
        # FR-004 / FR-029 / GI-014: the negative result a NON-LIVE filing is
        # answerable for, and explicitly `None` on a LIVE record rather than ""
        # — absent evidence and empty evidence are different claims, and a LIVE
        # record's reproduction lives in the description where the stream put
        # it.
        #
        # fallout AC-045 — `!= "LIVE"`, NOT `== "LATENT"`. HARDENING joined
        # DEFECT_TIERS this release and it is a probe that was DRIVEN and
        # failed, so it carries a reproduction exactly as LATENT does. The
        # equality spelling ACCEPTED a HARDENING filing for carrying one (the
        # shared validator demands it) and then persisted the record without it
        # — the field the F6 backlog calls the whole reason a HARDENING record
        # is trusted, dropped between the door that required it and the ledger.
        # Casting 4 fixed this expression at the single door and in
        # `retier_matching_untiered`; this is the third and last copy.
        "reproduction_attempted": reproduction_attempted if tier != "LIVE" else None,
        "description": description,
        "spec_ref": spec_ref,
        "symbol": symbol,
        "file": file_path,
        "status": "open",
        "fixed_in_cycle": None,
        # C-2 / GI-003: the three fields Foundry-Fix sets when this defect is
        # closed, seeded null at filing so every record has one shape from the
        # moment it exists. A reader asking "who fixed this and with what test"
        # gets `None` from an open defect rather than a KeyError.
        "regression_test": None,
        "authored_by": None,
        "fix_commit": None,
        "created_at": created_at if created_at is not None else now_iso(),
        # fallout FR-025 / CT-019 / ST-006 — THE TWO PROVENANCE KEYS, ALWAYS
        # BOTH, ON EVERY RECORD EITHER DOOR WRITES.
        #
        # Through casting 4's `defect_provenance`, which is the one place that
        # says what they are called and how they normalise. Written
        # UNCONDITIONALLY, `None` when the filing carried neither, and that is
        # the contract rather than tidiness: `foundry_state.fallout_rows` reads
        # an ABSENT `fallout_of` key as STRUCTURALLY UNMEASURED — never a
        # measured zero — so a door that wrote the key only when a filer set it
        # would make every cycle of every post-change run report
        # `not_measurable` forever, which is the reader certifying nothing while
        # looking like it certified something.
        **defect_provenance({"fallout_of": fallout_of, "supersedes": supersedes}),
    }
    if target_kind:
        record["target_kind"] = target_kind
    return record




# D-049 / CT-002 / AC-019 — what makes an incoming finding the SAME defect
# coming back.
#
# The matcher was `symbol == fixed.symbol OR description == fixed.description`,
# and a hit reopened the old record, DISCARDED the incoming finding entirely,
# and returned ok:true. Two drives at the MCP boundary:
#
#   - A prove/MISSING/FR-003 finding on symbol `submit_form` ("no CSRF
#     validation on the POST branch") was absorbed into a fixed
#     trace/UNWIRED/FR-001 record on the same symbol. added=0, reopened=1, one
#     record on disk still carrying the OLD source, type, spec_ref and
#     description. The new finding's four content fields were thrown away and
#     the caller was told the call succeeded.
#   - Description equality ALONE reopened a fixed defect across a different
#     file AND a different symbol.
#
# CT-002's contract is "records accepted and attributed to their true source"
# and AC-019's is "source attribution is preserved verbatim". Neither can hold
# for a record that was never written — this is worse than the source coercion
# the same function was fixed for, because coercion at least leaves a row.
#
# A regression is the same defect RECURRING, so identity is a conjunction:
#
#   1. No non-empty field may CONFLICT. A different symbol, file, type or
#      spec_ref means a different defect however much else agrees — that is
#      what killed the cross-file description match.
#   2. At least _REGRESSION_MIN_AGREEMENTS non-empty fields must AGREE. One
#      lone signal is a coincidence, not an identity — that is what killed the
#      same-symbol-different-everything match.
#
# Empty on either side is neither agreement nor conflict: an absent field
# carries no information and must not be allowed to manufacture either verdict
# (two records that both omit `file` have not thereby agreed about anything).
#
# Deliberately conservative, and its failure direction is the safe one: a
# genuine regression whose description was reworded between cycles is filed as
# a NEW defect — a record that exists, correctly attributed, at the cost of a
# `regressions` count that under-reports. The old failure direction was
# silent data loss on the highest-volume filing path in the protocol.
_REGRESSION_IDENTITY_FIELDS = ("symbol", "file", "type", "spec_ref", "description")


_REGRESSION_MIN_AGREEMENTS = 2




def _identity_value(raw) -> str:
    """Normalise an identity field for comparison: whitespace-folded, casefolded."""
    if not isinstance(raw, str):
        raw = "" if raw is None else str(raw)
    return " ".join(raw.split()).casefold()




def _is_regression_of(finding: dict, norm: dict, fixed_record: dict) -> bool:
    """Is ``finding`` the previously-fixed ``fixed_record`` recurring?

    See the block comment above for why this is a conjunction rather than the
    disjunction it replaces. ``norm`` carries the batch validator's canonical
    defect type so the incoming type is compared on the same footing as the
    stored one (MISPLACED and ARCHITECTURAL_PLACEMENT are one type under two
    spellings, and comparing raw would read them as a conflict).
    """
    agreements = 0
    for field in _REGRESSION_IDENTITY_FIELDS:
        if field == "type":
            incoming = _identity_value(norm.get("type"))
            existing = _identity_value(
                canonical_defect_type(fixed_record.get("type") or "") or ""
            )
        else:
            incoming = _identity_value(finding.get(field))
            existing = _identity_value(fixed_record.get(field))
        if not incoming or not existing:
            # Absent on either side: no information, so neither an agreement
            # nor a conflict.
            continue
        if incoming != existing:
            return False
        agreements += 1
    return agreements >= _REGRESSION_MIN_AGREEMENTS




@ledger_refusals
def foundry_sync_defects(
    cycle: int,
    findings: list[dict],
    project_root: str = ".",
) -> dict:
    """Sync new findings against existing defects. Detects regressions.

    FR-013 / CT-002 / NFR-002. This was the unvalidated door into the ledger:
    43 of grand-vulture's 168 defects (26%) entered through it. ``source`` was
    matched against a local set that agreed with neither the tool schema nor
    the stream vocabulary, and anything outside it was silently rewritten to
    "trace" \u2014 so a research_audit or coverage_diff finding was persisted as if
    TRACE had found it, and the run's evidence pointed at the wrong stream.
    ``type`` was written straight through with no validation at all.

    Both fields are now checked against the canonical vocabulary and the
    recorded source survives verbatim. Per A-035 the coercion was a bug, not a
    contract: values the old code accepted only by rewriting them are refused
    with a named error. NFR-002's no-narrowing guarantee covers calls that are
    valid under the reconciled vocabulary, and every such call still works.

    Validation is ALL-OR-NOTHING. A batch with one bad finding is refused whole
    rather than partly applied, so the caller never has to guess which of its
    findings landed.
    """
    from foundry_mcp.tools.foundry import (
        _dict_records,
        _observation_refusal,
        ledger_transaction,
        record_denylist_tripwire,
        retier_matching_untiered,
        tripwire_finding,
        validate_defect_filing,
    )

    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    defects_path = fdir / "defects.json"

    # --- Validate the whole batch before writing anything ------------------
    refusals: list[dict] = []
    normalized: list[dict] = []
    for i, finding in enumerate(findings):
        # CT-002 / D-064 — THE INPUT SIDE OF THE LOOP, GUARDED LIKE THE STORED
        # SIDE.
        #
        # D-128 celebrated closing exactly this class for the records ALREADY IN
        # the ledger (`_dict_records`) and left the caller's own list unguarded,
        # so `findings=['not-a-dict']` raised `AttributeError: 'str' object has
        # no attribute 'get'` on the very next line. `@ledger_refusals` does not
        # catch it, so server.py's outer net rendered "This is an unhandled
        # server-side error, not a refusal" — naming no index and no field,
        # against CT-002's requirement that the batch door refuse NAMING the
        # offending finding. `normalized` is appended to in the same breath so
        # the two lists stay index-aligned: the refusal loop below reads
        # `normalized[refused["index"]]["source"]`, and a `continue` that skipped
        # the append would make every later index name the wrong finding.
        if not isinstance(finding, dict):
            refusals.append({
                "index": i,
                "field": "finding",
                "value": repr(finding)[:120],
                "reason": (
                    f"findings[{i}] is {type(finding).__name__}, not an object. "
                    "Every finding is a mapping carrying at least source, type, "
                    "tier, defect_class and description."
                ),
            })
            normalized.append({
                "source": "", "type": None, "tier": None,
                "class": None, "reproduction_attempted": None,
            })
            continue

        source = finding.get("source", "")
        if not isinstance(source, str) or not source.strip():
            refusals.append({
                "index": i,
                "field": "source",
                "value": source,
                "reason": (
                    "source is required \u2014 an unattributed finding used to be "
                    "recorded as 'trace', which is the mis-attribution this "
                    "check exists to stop. Must be one of: "
                    f"{', '.join(sorted(DEFECT_SOURCE_IDS))}"
                ),
            })
        elif source.strip() not in DEFECT_SOURCE_IDS:
            refusals.append({
                "index": i,
                "field": "source",
                "value": source,
                "reason": (
                    f"Unknown source: {source}. Must be one of: "
                    f"{', '.join(sorted(DEFECT_SOURCE_IDS))}"
                ),
            })

        # fallout GI-022 / ST-006 (D-100) — AN ABSENT `type` IS REFUSED, NOT
        # COERCED ONTO `MISSING`.
        #
        # This read `finding.get("type") or "MISSING"`, and MISSING is a real
        # member of `DEFECT_TYPES` — so a finding that named no type at all was
        # filed as one that named the WRONG one. Driven on the two doors:
        # `foundry_add_defect(defect_type="")` returns
        # {'error': "Invalid defect_type: ''...", 'field': 'defect_type'};
        # `foundry_sync_defects` with no `type` key, and again with `type: ""`,
        # both returned {'ok': True, 'added': 1} and both persisted
        # `type: "MISSING"`. Same finding, two doors, opposite outcomes — the
        # door divergence ST-006 and GI-022 are stated over.
        #
        # IT IS LOAD-BEARING BEYOND ATTRIBUTION. `type` is one of the four
        # identity fields `retier_matching_untiered` matches on (source, type,
        # file, symbol), so a finding filed batch-side as MISSING can never be
        # re-tiered by a later single-door filing that names its real type: the
        # untiered record stays open, blocking, with no cheap exit — which is
        # the exact harm FR-051's re-tier branch exists to remove.
        #
        # This is the `source -> "trace"` coercion one field along, and it is
        # closed the same way: refused by name, never rewritten. The absent and
        # the unknown case are told apart in the sentence, exactly as the
        # `source` rung above tells them apart, because "you left it out" and
        # "that is not a member" send the filer to different places.
        raw_type = finding.get("type")
        canonical = canonical_defect_type(raw_type)
        if canonical is None:
            refusals.append({
                "index": i,
                "field": "type",
                "value": raw_type,
                "reason": (
                    (
                        "type is required — an unattributed finding used to be "
                        "recorded as 'MISSING', which is a real member of the "
                        "set and therefore a wrong answer rather than an empty "
                        "one. Must be one of: "
                        f"{', '.join(sorted(DEFECT_TYPES))}"
                    )
                    if not (isinstance(raw_type, str) and raw_type.strip())
                    else (
                        f"Unknown defect_type: {raw_type}. Must be one of: "
                        f"{', '.join(sorted(DEFECT_TYPES))}"
                    )
                ),
            })

        # CT-001 / CT-002 / CT-003 — THE TIER, CLASS AND LATENT CHECKS, DECIDED
        # BY THE SHARED VALIDATOR AND NOT BY THIS DOOR.
        #
        # There are two filing doors in two modules — `foundry_add_defect` in
        # tools/foundry.py and this one — and every check they were each trusted
        # to remember has eventually diverged. D-119 is the shipped instance:
        # the two disagreed about which cycle a record belonged to, so identical
        # findings filed through different doors produced different cycle runs
        # and a systemic class escaped ST-002 escalation entirely. A filing whose
        # tier the single door demands and the batch door does not is that defect
        # one field along, and WORSE — this is the door a whole INSPECT stream
        # files through, so the gap would be the common path rather than the rare
        # one.
        #
        # `validate_defect_filing` is therefore called, never re-implemented. It
        # owns the locked check order — the security denylist FIRST (D-061 moved
        # it there: both doors fire `record_denylist_tripwire` only on a refusal
        # carrying `denylist_class`, so a security claim that tripped the tier
        # rung first was refused with no audit record at all), then tier, then
        # class, then reproduction_attempted — so both doors name the same field
        # first for the same
        # bad filing, and it reads the mapping and nothing else — no ledger, no
        # run dir — which is what lets this door run it once per finding BEFORE
        # opening its transaction. A refusal here costs nothing and writes
        # nothing.
        #
        # Stated AFTER the source/type rungs so a finding that is wrong in both
        # ways still names `source` first, exactly as it did before this landed.
        #
        # D-098 — ONE PIPELINE ORDER, OR THE TWO DOORS ARE NOT ONE RULE.
        # -------------------------------------------------------------
        # The comment-prose rung sat at a DIFFERENT POINT in each door's
        # pipeline, so the two doors disagreed about what a defect is.
        # `foundry_add_defect` runs `_observation_refusal` BEFORE
        # `validate_defect_filing` and long before the re-tier; this door ran
        # `validate_defect_filing`, then `retier_matching_untiered`, and reached
        # its declared-comment branch only for findings that matched neither.
        #
        # Driven on one seeded ledger holding an open untiered D-001, with a
        # `target_kind="comment"` LATENT finding whose description is
        # ENUMERATION-classed and whose (source, type, file, symbol) matches
        # D-001: `Foundry-Defect` returned "Refused: ENUMERATION is a
        # comment-prose observation class, not a defect" and left D-001 open and
        # untiered; `Foundry-Sync` returned {'ok': true, 'retiered': 1,
        # 'retiered_ids': ['D-001']} and D-001 came out carrying LATENT. Same
        # finding, same ledger, opposite outcomes — one door refused it as an
        # observation, the other converted a blocking untiered record into a
        # tracked defect and reported success. Driven again with tier='MEDIUM'
        # on the same finding, `Foundry-Defect` named the observation class and
        # this door named `tier`, contradicting `validate_defect_filing`'s own
        # pinned contract ("THE CHECK ORDER IS LOCKED, so that the two doors
        # name the same field first for the same bad filing") and reproducing
        # the exact drift its "WHY THIS IS A SHARED FUNCTION" section exists to
        # prevent.
        #
        # LEAD RULING, GRIND cycle 6: ONE order for both doors, hosted in one
        # place both call — comment-prose refusal, security denylist, tier,
        # class, reproduction_attempted, re-tier match, persist. So the rung
        # runs HERE, in the pre-transaction validation loop, ahead of
        # `validate_defect_filing`: putting it merely ahead of the re-tier
        # branch would not have closed the field-order half, because the tier
        # rung fires in this loop and the re-tier branch is two frames later,
        # inside the transaction.
        #
        # `_observation_refusal` is CALLED, not re-implemented — the same
        # function `foundry_add_defect` calls, so the two doors cannot drift
        # about which findings are comment prose. Its own three guards
        # (declared-comment subject, denylist outranks, promote-direction
        # fail-safe) all still apply, so a security claim, a spec-required
        # behaviour claim or a finding asserting what the code does still
        # reaches the tier rung below and is still filed as a defect.
        #
        # THE REFUSAL TEXT IS THE OTHER DOOR'S, VERBATIM. "Identical outcomes"
        # is the ruling's test, and a stream that files the same finding through
        # either door now reads the same sentence and is sent to the same place.
        refused_class = _observation_refusal(finding)
        if refused_class is not None:
            refusals.append({
                "index": i,
                "field": "description",
                "value": finding.get("description", ""),
                "reason": (
                    f"Refused: {refused_class} is a comment-prose observation "
                    f"class, not a defect. The comment-prose classes are: "
                    f"{', '.join(sorted(OBSERVATION_CLASSES))}."
                ),
                "refused_class": refused_class,
                "observation_classes": sorted(OBSERVATION_CLASSES),
            })
            # The tier rung is SKIPPED for it, exactly as it is skipped on the
            # other door: a finding that is comment prose is not a defect at
            # all, and telling its filer about a missing tier would send them to
            # add a field to a record that should never reach this ledger.
            # `normalized` is still appended below, because the refusal loop and
            # the write loop both index into it.
            filing_problem = None
        else:
            filing_problem = validate_defect_filing(finding)
        if filing_problem is not None:
            refusals.append({
                "index": i,
                "field": filing_problem.get("field", "tier"),
                "value": finding.get(filing_problem.get("field", "tier")),
                "reason": filing_problem.get("error", ""),
                **(
                    {"denylist_class": filing_problem["denylist_class"]}
                    if "denylist_class" in filing_problem
                    else {}
                ),
            })

        declared_class = finding.get(DEFECT_CLASS_FIELD)
        reproduction = finding.get("reproduction_attempted")
        normalized.append({
            "source": source.strip() if isinstance(source, str) else source,
            "type": canonical,
            "tier": finding.get("tier"),
            "class": (
                declared_class.strip() if isinstance(declared_class, str) else declared_class
            ),
            "reproduction_attempted": (
                reproduction.strip() if isinstance(reproduction, str) else reproduction
            ),
        })

    if refusals:
        # CT-003 \u2014 THE SECURITY REFUSAL ALSO WRITES THE TRIPWIRE.
        #
        # A LATENT filing matching the security-property predicate is refused,
        # and the ATTEMPT is what the audit record exists to capture: a stream
        # trying to file a security claim as a gap it merely reasoned about is
        # the thing an auditor needs to see, and the refusal alone leaves no
        # trace of it. Fired through the ONE exported writer that
        # `foundry_add_defect` and `foundry_add_observation` also call \u2014 a
        # second writer here would be an audit control that only records the
        # attempts one of its callers makes.
        #
        # D-147 / D-146 — AND THE RECORD NAMES THE CLASS THE REFUSAL NAMED.
        #
        # `record_denylist_tripwire` does not receive the refusal's class; it
        # RE-DERIVES one through `vocab.never_demote_class`, whose security
        # entry reads `description` alone, while the refusal above keys on ALL
        # the prose a filing carries. So a finding whose security claim lives
        # in some other key was refused SECURITY_PROPERTY_CLAIM and audited
        # NON_COMMENT — one event, two artifacts that contradict each other,
        # which is exactly what D-083 pinned may never happen. `foundry_add_defect`
        # already wraps its finding; this door did not, and two doors trusted
        # to remember one step each is the arrangement that keeps diverging.
        for refused in refusals:
            if refused.get("denylist_class"):
                record_denylist_tripwire(
                    fdir,
                    tripwire_finding(findings[refused["index"]]),
                    cycle=current_cycle(fdir),
                    source=normalized[refused["index"]]["source"],
                )
        return {
            "error": (
                f"Refused {len(refusals)} finding(s) \u2014 no findings were recorded. "
                + "; ".join(f"findings[{r['index']}].{r['field']}: {r['reason']}" for r in refusals)
            ),
            "refusals": refusals,
            "hint": (
                "Fix the named fields and re-send the whole batch. Values are "
                "never coerced onto a known member \u2014 a finding attributed to the "
                "wrong stream is worse than a finding refused."
            ),
        }

    # The cycle a defect is stamped with is the SERVER's (FR-005). A
    # caller-asserted cycle cannot be trusted for persistence: the whole
    # three-cycle escalation rule reads these numbers back.
    server_cycle = current_cycle(fdir)

    reopened = 0
    added = 0
    retiered = 0
    retiered_ids: list[str] = []
    regressions: list[str] = []
    tripwires: list[dict] = []

    # One exclusive critical section over defects.json for the whole batch.
    # AC-025's uniqueness guarantee comes from THIS lock: allocate_record_id is
    # pure, so minting inside the transaction is what stops two concurrent
    # filers reading the same snapshot and claiming the same id.
    with ledger_transaction(defects_path, "defects") as records:
        # D-128 — the half of D-097 that was never applied.
        #
        # `_dict_records` was added to foundry.py BY D-097, with the docstring
        # "Tolerating it in ONE place is what keeps the two halves of the same
        # scan consistent", and then applied to the single door only. This
        # batch door kept its raw `d.get(...)` over every historical record.
        # Driven with {"defects": [{"id":"D-001",...}, "not-a-dict"]}:
        # Foundry-Defect persisted D-002 and preserved the malformed record,
        # Foundry-Defects read clean, and Foundry-Sync raised AttributeError
        # 'str' object has no attribute 'get'. The raise lands AFTER the
        # in-transaction list is mutated, so `_save_json` never runs, the
        # filing the stream just made is GONE, and the escaped traceback names
        # no file -- the one row breaking D-095/D-098's 21/24 named-refusal
        # bar.
        #
        # THE PRIMITIVE NOW CLOSES THIS TOO. D-127 landed in foundry.py during
        # this same cycle and moved the filter INSIDE `ledger_transaction`,
        # which yields mapping records only and re-inserts the non-dicts by
        # index before the write. So this call is idempotent today rather than
        # load-bearing, and it is kept deliberately on both counts: it is what
        # the scan in test_orchestrator_gates.py asserts (an orchestrator that
        # iterates the binding directly has bypassed the guarantee however
        # careful its body is), and it keeps this door's correctness legible
        # here instead of resting silently on a sibling module's internals.
        # Two guards on one class from both sides is the shape the escalated
        # class asks for; one guard in a file this casting may not edit is not.
        fixed = [d for d in _dict_records(records) if d.get("status") == "fixed"]

        # fallout CT-019 / AC-045 — THE `fallout_of` RUNG, INSIDE THE LOCK AND
        # AHEAD OF EVERY APPEND.
        #
        # It is the one rung `validate_defect_filing` cannot own: that function
        # reads the mapping and nothing else, by contract, precisely so this
        # door can call it once per finding BEFORE opening a transaction. "Does
        # the ledger hold this id" is a LEDGER question, and asking it outside
        # the lock would re-open the read-then-write window the lock exists to
        # close — a parent filed by a concurrent door would read as unknown.
        #
        # SWEPT OVER EVERY FINDING FIRST, which is what makes this door's
        # all-or-nothing refusal survive the addition. The single door returns
        # from inside its transaction knowing nothing has been mutated yet; this
        # one appends in a loop, so a rung that fired on the fourth finding
        # after three appends would commit three of a batch the caller was told
        # was refused. `test_sync_refusal_is_all_or_nothing` is the pin, and it
        # is about exactly this.
        superseded: list[str] = []

        # fallout ST-006 / GI-022 (D-099) — ONE SPELLING OF THE CLOSURE FOR BOTH
        # OF THE WRITE LOOP'S EXITS.
        #
        # The loop below leaves by two paths — a re-tier of an existing untiered
        # record, or an append of a new one — and the closure was written under
        # the append alone, BELOW a `continue` the re-tier path took. So a
        # promotion filed batch-side left its HARDENING parent open. Driven on
        # identical ledgers (open HARDENING D-001 + open untiered D-002,
        # identical finding, supersedes="D-001"): `foundry_add_defect` returned
        # {'retiered_ids': ['D-002'], 'superseded': 'D-001', 'open_defects': 1}
        # with D-001 `superseded`; `foundry_sync_defects` returned
        # {'retiered_ids': ['D-002'], 'superseded_ids': [], 'total_open': 2} with
        # D-001 still `open` — so it kept blocking every `status == "open"`
        # census, but only on the batch path.
        #
        # The single door has no `continue` there: it sets `defect_id` on each
        # branch and FALLS THROUGH to one closure. This is the same shape said
        # as a closure over the loop's own locals, so the two exits cannot come
        # to disagree by anyone indenting one of them — which is the mistake
        # that produced the divergence in the first place.
        #
        # `defect_provenance` normalises the cite on BOTH paths (the append path
        # gets it through `new_defect_record`), so the re-tier path cannot come
        # to accept a spelling the append path refuses.
        def _close_promotion(cite: object, by_id: str) -> None:
            closed = close_superseded_record(
                records, cite, by_id=by_id, cycle=server_cycle
            )
            if closed is not None:
                superseded.append(closed)

        for finding in findings:
            unknown_parent = fallout_parent_problem(
                finding.get("fallout_of"), records
            )
            if unknown_parent is not None:
                return unknown_parent

        for finding, norm in zip(findings, normalized):
            symbol = finding.get("symbol", "")
            desc = finding.get("description", "")

            match_id = None
            for fd in fixed:
                if _is_regression_of(finding, norm, fd):
                    match_id = fd["id"]
                    break

            if match_id:
                for d in _dict_records(records):
                    if d.get("id") == match_id:
                        d["status"] = "open"
                        d["regression"] = True
                        d["reopened_in_cycle"] = server_cycle
                        d["fixed_in_cycle"] = None
                        break
                reopened += 1
                regressions.append(match_id)
                continue

            # FR-051 / D-062 — "BLOCKS LIKE LIVE UNTIL A STREAM RE-FILES IT
            # WITH A TIER", IMPLEMENTED AS AN ACTUAL EXIT.
            #
            # `_blocking_defects` tells the lead to "have the filing stream
            # re-file each untiered defect with tier=LIVE or tier=LATENT", and
            # following that hint made the ledger strictly worse: the batch door
            # only ever reopened a record already `fixed` or appended a new one,
            # so the identical finding came back as a SECOND open record beside
            # the untiered one. Driven: one open untiered D-001, the same finding
            # re-filed with tier LATENT -> {'added': 1, 'total_open': 2},
            # `blocking` unchanged at 1. Every `tier` write in src/ was on
            # new-record construction; no branch updated an existing open record.
            # A resumed pre-change run therefore had no cheap exit at all — only
            # Foundry-Fix, which resolves such a record to TIER_UNKNOWN and
            # demands the full LIVE ceremony on a finding no stream classified.
            #
            # Identity is (source, type, file, symbol): the four fields that say
            # WHICH finding this is. The description is deliberately excluded —
            # a re-filing stream rewrites its prose, and requiring the wording to
            # match would make the exit unreachable for the same reason the hint
            # was. The record KEEPS ITS ID, so every citation and every task
            # already naming it stays valid, and `retiered_in_cycle` records when
            # the classification arrived.
            # D-077 — ONE RULE, TWO DOORS, ONE IMPLEMENTATION.
            #
            # The match-and-re-tier rule lived twice: this loop, and a second
            # copy inside `foundry_add_defect`. Two copies of "which stored
            # record IS this finding" is the same drift shape the tier read, the
            # sweep, the handoff writer and the report generator are each
            # deliberately owned by ONE module — and it is worse here, because
            # the two doors disagreeing means a stream's exit from an untiered
            # record depends on WHICH door it happened to file through.
            # `foundry.retier_matching_untiered` is now that one implementation
            # (casting 2 owns `tools/foundry.py`); this door calls it and
            # re-implements nothing. It returns the id of the record it
            # re-tiered, or None when no open untiered record matches on
            # (source, type, file, symbol).
            # fallout D-101 / FR-025 / CT-019 / ST-006 (concern C-070) — THE
            # RE-FILING'S PROVENANCE REACHES THE RECORD IT CLASSIFIES.
            #
            # Casting 4's D-101 fix gave `retier_matching_untiered` the two
            # provenance fields and DEFAULTED them so this door kept compiling
            # while it was repointed. Defaulted is not passed: left unrepointed,
            # a batch re-filing that declared `fallout_of` or `supersedes` would
            # classify the record and drop both, so the same finding through the
            # single door and through this one would leave differently-measured
            # records — the door divergence D-099 and D-100 are about, one field
            # set along.
            #
            # Through `defect_provenance`, the same one spelling the append path
            # gets via `new_defect_record` and the closure above gets for its
            # cite, so a third provenance field joining the contract reaches all
            # three by construction.
            provenance = defect_provenance(finding)
            retier_id = retier_matching_untiered(
                records,
                source=norm["source"],
                type=norm["type"],
                file=finding.get("file") or "",
                symbol=symbol or "",
                tier=norm["tier"],
                reproduction_attempted=norm["reproduction_attempted"],
                defect_class=norm["class"],
                cycle=server_cycle,
                fallout_of=provenance["fallout_of"],
                supersedes=provenance["supersedes"],
                # fallout GI-004 / D-157 (casting 4's concern C-075) — THE AUDIT
                # HALF, AT THIS DOOR TOO.
                #
                # D-157 gave the re-tier a tier guard, and its ENFORCEMENT half
                # reached both doors with no call-site change: the guard raises
                # `LedgerRefusal`, the transaction writes nothing, and
                # `@ledger_refusals` turns it into the house refusal here
                # exactly as at `foundry_add_defect`. Its AUDIT half did not.
                # `record_denylist_tripwire` needs the resolved run dir, which a
                # pure list mutator does not hold, so the guard takes `fdir` —
                # DEFAULTED, for the same reason casting 4 defaulted
                # `fallout_of` and `supersedes` on this same function (C-070):
                # so this file kept compiling while it was repointed. Defaulted
                # is not passed. Left unrepointed, a denylisted claim re-tiered
                # through the BATCH door was refused and NOT audited, and D-061's
                # ruling is that the tripwire may not be rung-dependent —
                # door-dependent is the same defect one axis over, and this is
                # the door a whole INSPECT stream files through.
                #
                # `fdir` is already in scope: `get_run_dir(project_root)` at the
                # top of this function, and `defects_path` is derived from it.
                # Nothing new is resolved and no path is re-derived.
                fdir=fdir,
            )
            if retier_id is not None:
                retiered += 1
                retiered_ids.append(retier_id)
                # fallout ST-006 / GI-022 (D-099) — THE EXIT THE CLOSURE USED TO
                # SKIP. A re-filing that CLASSIFIES an untiered record is still a
                # new filing, and a filing that cites a HARDENING id promotes it
                # whichever of the two records ends up carrying the finding. The
                # single door reaches its one closure from this branch by
                # falling through; this one reaches the same closure by name.
                _close_promotion(provenance["supersedes"], retier_id)
                continue

            # Comment-prose findings are OBSERVATIONS, not defects, and are
            # refused from this ledger. Four rules apply, in this order, and
            # they are the SAME rules the Foundry-Defect filing path applies —
            # two filing paths that disagree about what a defect is would be a
            # worse bug than the one being fixed:
            #
            #   1. The subject must be a DECLARED comment. An absent
            #      target_kind does not license a demotion: vocab's
            #      is_non_comment only matches a target_kind that is present
            #      and non-"comment", so absence has to be handled here or the
            #      NON_COMMENT denylist entry silently never fires.
            #   2. A denylist match OUTRANKS an observation match (vocab's
            #      precedence rule) — a security claim, a spec-required-
            #      behaviour claim or an unresolvable cite stays a defect even
            #      when its prose reads like drift.
            #   3. A finding that ASSERTS WHAT THE CODE DOES is not confined to
            #      comment prose, so no comment-prose refusal may fire against
            #      it however its wording reads (D-094).
            #   4. Only then does the observation class decide.
            #
            # If the ledger writer refuses the demotion anyway, its refusal
            # wins and the finding stays a defect too: AC-002 is a never-weaken
            # guarantee, so every branch fails safe toward "defect".
            declared_comment = (
                isinstance(finding.get("target_kind"), str)
                and finding["target_kind"].strip().lower() == "comment"
            )
            if declared_comment:
                # D-036 — the denylist decision AND its audit signal are ONE
                # exported call.
                #
                # This read `never_demote_class(finding) is None` and skipped
                # the whole branch on a match. The finding correctly stayed a
                # defect, but nothing downstream ran, so
                # `record_denylist_tripwire` — which tools/foundry.py exports
                # precisely for this call site, and whose own docstring names
                # it — never fired on the Sync path. Live-proved: a
                # SECURITY_PROPERTY_CLAIM comment finding filed through
                # Foundry-Sync stayed a defect (the enforcement half, correct)
                # and left observations.json's `tripwire` empty (the audit
                # half, dead). An audit signal that fires only for the filing
                # path that did not need auditing is not a control.
                #
                # Calling the helper makes the same decision the local check
                # made and writes the signal as it does. Its NON_COMMENT
                # fallback cannot fire under this guard — the helper's
                # `_subject_is_declared_comment` is the same predicate as
                # `declared_comment` above — so a non-None return means, still
                # and only, "a denylist entry matched: keep this a defect".
                #
                # It is scoped to declared_comment deliberately. A finding with
                # no `target_kind` is not attempting a demotion (Sync has no
                # classification argument, so target_kind is the only demotion
                # signal on this path), and auditing those would fire a
                # NON_COMMENT tripwire on every ordinary defect and bury the
                # real ones.
                denied = record_denylist_tripwire(
                    fdir, finding, cycle=server_cycle, source=norm["source"]
                )
                if denied is not None:
                    tripwires.append(denied)
                # D-098 — THE DEMOTION BRANCH IS GONE; THE AUDIT SIGNAL STAYS.
                #
                # What stood here was an `elif asserts_code_behaviour(finding)`
                # pass-through (D-094's promote-direction fail-safe) and an
                # `else` that routed the finding into `observations.json` and
                # `continue`d. Both are now decided one frame earlier, by
                # `_observation_refusal` in the validation loop above — which
                # applies the SAME three guards in the SAME order — so a finding
                # that reaches this line is one that rung already let through:
                # it is denylisted, it asserts what the code does, or its prose
                # matches no observation class. All three are DEFECTS, and
                # falling out of this branch into the append below is what each
                # of them means.
                #
                # The routing had to go because it was the last thing making the
                # two doors disagree: `foundry_add_defect` REFUSES comment prose
                # and names Foundry-Observation, and a batch door that silently
                # wrote it somewhere else instead was not the same rule applied
                # twice. Streams file comment prose through Foundry-Observation,
                # which is the channel `agents/*.md` already instructs and which
                # the refusal above names.
                #
                # `record_denylist_tripwire` stays exactly where D-036 put it.
                # It is the AUDIT half, not the enforcement half: it fires for a
                # declared-comment finding a denylist entry rescued, which is
                # precisely the case `_observation_refusal` returns None for and
                # therefore never refuses. Removing it would re-open D-036 — an
                # audit signal that fires only for the filing path that did not
                # need auditing.

            # C-2 / CT-001 / D-192 — THE SHAPE IS `new_defect_record`'S, NOT A
            # SECOND LITERAL THAT MERELY CLAIMS TO MATCH IT.
            #
            # What stood here was the same eighteen keys hand-typed a second
            # time, under a comment asserting "the batch door writes the SAME
            # record shape as the single door, field for field". The assertion
            # was false for `target_kind`, which the single door persisted and
            # this one dropped — see `new_defect_record` for the driven
            # divergence and for why a comment is not a mechanism.
            #
            # `validate_defect_filing` has already refused an absent or unknown
            # tier, an absent class and a LATENT filing with no negative result,
            # so every value passed below is a validated one.
            defect = new_defect_record(
                record_id=_mint_defect_id(records),
                cycle=server_cycle,
                declared_cycle=cycle,
                source=norm["source"],
                defect_type=norm["type"],
                tier=norm["tier"],
                defect_class=norm["class"],
                reproduction_attempted=norm["reproduction_attempted"],
                description=desc,
                spec_ref=finding.get("spec_ref", ""),
                symbol=symbol,
                file_path=finding.get("file", ""),
                # D-192: the declaration the caller made, carried ONTO the
                # record. This door read `target_kind` only as a demotion signal
                # (the `declared_comment` branch above) and then discarded it,
                # so a Sync-filed defect could not be audited for a declaration
                # the same finding preserves when it is filed one door over.
                target_kind=finding.get("target_kind") or "",
                created_at=now_iso(),
                # fallout FR-025 / CT-019 / ST-006: the caller's two provenance
                # fields, carried onto the record. `new_defect_record` writes
                # both keys whatever these are, so a Sync-filed record is
                # measurable on the same terms a Defect-filed one is.
                fallout_of=finding.get("fallout_of"),
                supersedes=finding.get("supersedes"),
            )
            records.append(defect)
            added += 1

            # fallout ST-006 / GI-022 — THE PROMOTION, IN THE SAME TRANSACTION
            # THAT PERSISTED THE RECORD MAKING IT.
            #
            # A closure written in a second transaction would leave a window in
            # which the citing record exists and the record it supersedes is
            # still open, and every gate and census that counts `status ==
            # "open"` reads that window as one more open defect than the run
            # has. After the append, because the closure records the id of the
            # record that superseded it and that id is minted above.
            #
            # The TIER is not touched (OT-020 / GI-022): promotion is a NEW
            # filing that CITES the earlier record, never a rewrite of what a
            # stream said it saw.
            _close_promotion(defect["supersedes"], defect["id"])

        total_open = sum(1 for d in _dict_records(records) if d.get("status") == "open")

    if regressions:
        forge_log = fdir / "forge-log.md"
        if forge_log.exists():
            with open(forge_log, "a", encoding="utf-8") as f:
                f.write(f"\n### REGRESSIONS in cycle {server_cycle}\n")
                for r in regressions:
                    f.write(f"- **{r}** reopened \u2014 fix was fragile\n")
                f.write("\n")

    result = {
        "ok": True,
        "cycle": server_cycle,
        "declared_cycle": cycle,
        "added": added,
        "reopened": reopened,
        # D-098: `observations` and `observed` are GONE from this result. They
        # counted a demotion this door no longer performs — comment prose is
        # refused here exactly as it is at `foundry_add_defect`, and the filer
        # is named Foundry-Observation. Reporting a permanently-zero count of a
        # thing that cannot happen is how a reader concludes the channel is
        # merely quiet.
        "regressions": regressions,
        # FR-051 / D-062: re-filings that CLASSIFIED an existing untiered record
        # rather than appending a duplicate beside it. Reported so a lead
        # following `_blocking_defects`' hint can see the exit happened.
        "retiered": retiered,
        "retiered_ids": retiered_ids,
        # fallout ST-006: the open HARDENING records this batch PROMOTED, named
        # where the filer reads them. A closure the caller cannot see is a
        # closure it will try to make again.
        "superseded_ids": superseded,
        "total_open": total_open,
    }
    if tripwires:
        result["denylist_tripwires"] = tripwires
    return result


# --------------------------------------------------------------------------- #
# fallout GI-033 / AC-061 / FR-063 (D-021 / D-035) — MOVED HERE FROM
# `orchestration/width.py`, WHERE THEY WERE LODGERS.
#
# `width.py` is in the VERIFIER set and this module is not, so every import of
# a width symbol from here was a lifecycle-to-verifier edge — the direction
# GI-033's violation column refuses with NO exception at all — excused by a
# guard's allowlist row instead of removed. Neither symbol is a width fact:
# `_decode_git_path` is git's own quoting grammar and is reached only by the
# `git show --numstat` parse below, and `_note_fix_after_inspect_decision` is
# what `foundry_mark_defect_fixed` stamps when a fix lands mid-INSPECT. Their
# SOLE consumer is this module, so they move to it and the edge goes with them.
# --------------------------------------------------------------------------- #

#: D-238 — git's C-quote escapes, for the one place the quoted form survives.
#:
#: `core.quotepath=false` stops git escaping non-ASCII bytes, and that is the
#: whole of the common case. It does NOT stop git quoting a path that contains a
#: double quote, a backslash or a control character: those are quoted whatever
#: `quotepath` says, because the quoting is what keeps such a path on ONE line.
#: So a parse that reads git's LINE-oriented output still meets the quoted form
#: and still has to undo it.
_C_QUOTE_ESCAPES = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
    "v": "\v", "\\": "\\", '"': '"',
}




def _decode_git_path(field: str) -> str:
    """One path as git PRINTS it, back to the path it NAMES (D-238).

    A field that is not quoted is returned unchanged, so this is safe to apply
    to every path git hands back rather than only to the ones a caller guessed
    might need it. A quoted field is unwrapped and its escapes are undone —
    octal escapes accumulate as BYTES and are decoded as UTF-8 at the end,
    because git emits one escape per byte and a multi-byte character therefore
    arrives as several (`\\303\\251` is one `é`, not two characters).

    WHY THIS EXISTS RATHER THAN A SECOND FLAG (D-238 / D-239's shared class).
    Where git can be asked for NUL-separated output it is (`git_changed_paths`),
    and then nothing is ever quoted and this is never reached. `git show
    --numstat` is the one consumer that cannot take that route without changing
    its record grammar — with `-z` a rename becomes three NUL-separated tokens
    rather than one tab field — so it keeps the line grammar
    `_numstat_rename_paths` parses, passes `core.quotepath=false`, and undoes
    the residual quoting here.
    """
    if not isinstance(field, str):
        return ""
    if len(field) < 2 or not (field.startswith('"') and field.endswith('"')):
        return field
    body = field[1:-1]
    out = bytearray()
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.extend(ch.encode("utf-8"))
            i += 1
            continue
        i += 1
        if i >= len(body):
            # A trailing lone backslash is not an escape git would emit; keep
            # it rather than dropping a byte the path may really carry.
            out.extend(b"\\")
            break
        esc = body[i]
        if esc in "01234567":
            digits = ""
            while i < len(body) and len(digits) < 3 and body[i] in "01234567":
                digits += body[i]
                i += 1
            out.append(int(digits, 8) & 0xFF)
            continue
        out.extend(_C_QUOTE_ESCAPES.get(esc, esc).encode("utf-8"))
        i += 1
    return out.decode("utf-8", errors="replace")




def _note_fix_after_inspect_decision(fdir: Path, defect_id: str) -> None:
    """Mark the open INSPECT's recorded width as superseded by a fix (D-035).

    D-035 — A DELTA-SWEPT INSPECT COULD OPEN ASSAY.
    ----------------------------------------------
    `foundry_mark_defect_fixed` has no phase guard, so a fix landing while the
    run sits in F2 flips the blocking count to zero AFTER the width was already
    decided and the sweep already taken. `inspect_clean` then passes and ASSAY
    opens on a cycle whose sweep never covered the surface that fix changed —
    the one crossing GI-002 exists to make honest.

    Recorded rather than refused. The fix itself is legitimate work and
    refusing it would push the lead to fix the defect and not say so, which is
    strictly worse. What is not legitimate is CARRYING that cycle's decision
    forward as though it still described the tree, so the entry is stamped and
    `inspect_clean` refuses until a fresh `inspect_start` re-decides the width
    and re-sweeps at the new HEAD.

    A no-op outside F2: in F3 GRIND, which is where fixes normally land, the
    next `inspect_start` decides a width that already accounts for them.
    """
    state_path = fdir / "state.json"
    with _document_transaction(state_path) as state:
        if state.get("phase") != "F2":
            return
        modes = state.get("inspect_modes")
        if not isinstance(modes, list) or not modes:
            return
        current = modes[-1]
        if not isinstance(current, dict):
            return
        superseded = current.get("fixes_after_decision")
        if not isinstance(superseded, list):
            superseded = []
        if defect_id not in superseded:
            superseded.append(defect_id)
        current["fixes_after_decision"] = superseded
        state["inspect_modes"] = modes
        state["updated_at"] = now_iso()

