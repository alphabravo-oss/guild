"""READ THIS BEFORE WRITING A REQUIREMENT ID IN ANY TEST MODULE HERE.

This run has THREE specs installed side by side, and they number their rows
identically:

  * ``forge-specs/foundry-run-process-fixes/spec.md``
  * ``forge-specs/foundry-run-convergence/spec.md``
  * ``forge-specs/foundry-run-fallout/spec.md``

87 requirement ids exist in BOTH, across all eight families this suite cites
(``US GI AC OT FR CT ST NFR``) -- every id the earlier spec numbers, the later
one numbers too, with different text. So a BARE id in a docstring or a comment
names no spec at all. It reads as a citation and resolves to two different
requirements, and which one a reader lands on is decided by which spec they
happened to open.

THE CONVENTION, and it is TOTAL: every requirement id in prose carries its
spec.

    process-fixes AC-001     cites forge-specs/foundry-run-process-fixes
    convergence AC-006       cites forge-specs/foundry-run-convergence
    fallout AC-056           cites forge-specs/foundry-run-fallout

Three spellings, no fourth, and no default. A ``/``-joined run inherits the
qualification of its head THROUGH REQUIREMENT IDS AND NOTHING ELSE: every
segment between the qualification and the id has to itself be a requirement id
of one of the families above, which is why ``convergence CT-002 / AC-019 /
OT-008`` qualifies all three and is how section headers are written. A
RUN-LOCAL id in the run -- a concern ``C-NNN``, a defect ``D-NNN`` -- is not one
of those families, so it ENDS the inheritance where it stands and every
requirement id after it is reported however the run began. ``fallout AC-022 /
C-081 / GI-002`` is qualified at its head and still reports its last id; put
the requirement ids first and the run-local ids last, ``fallout AC-022 /
GI-002 / C-081``, or give each id its own qualification. The refusal prints
this as ``CHAIN_PHRASE`` and the two runs it names are driven, so the one it
calls broken is a run that actually breaks. Ids in CODE are not
citations and are never scanned: a ``spec_ref=`` fixture literal, a
``parametrize`` entry, a module constant, an assertion's expected string. Those
are input handed to a door under test, not a claim about which requirement a
test proves.

AN ASSERTION'S MESSAGE IS PROSE, AND THE COMMA IS WHERE THE TWO PART. In
``assert lhs == rhs, "..."`` the ``rhs`` is data and stays unscanned; the
message after the comma is the sentence a failing engineer reads, makes exactly
the claim this convention governs, and is scanned like a docstring. It was not,
until this cycle, and that gap IS D-195: a module held to the full convention
passed every run while carrying a bare ``CT-001`` in a message, and the id
resolves to the convergence spec's tier contract under one spec and to
``Foundry-Concern`` under this one. Casting 11 measured 209 such ids across
``tests/`` and closed its own; ``assertion_messages`` below is the detector.

This module owns the machinery that enforces that, and the two roster pins that
apply it -- one per prose surface -- to every module in this directory.
"""

from __future__ import annotations

import ast
import functools
import io
import re
import tokenize
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent

#: Every requirement-id family the two specs number identically. Measured, not
#: assumed: the process-fixes spec's id set is a strict SUBSET of the
#: convergence spec's across all eight, so there is no family where a bare id
#: happens to be safe.
ID_FAMILIES = frozenset(
    {"US", "GI", "AC", "OT", "FR", "CT", "ST", "NFR"}
)  # 8 items

#: What the scan matched before this cycle -- ``US`` and ``GI`` were absent
#: from it, so a bare ``US`` id was invisible. That blind spot IS D-185: the
#: tag it was filed against leads with one, so only the second half of it could
#: ever have been reported. Correctly written that tag reads
#: ``process-fixes US-002 / FR-004``.
#:
#: Kept as a declared set rather than deleted because ``tests/test_escalation.py``
#: (casting 3's) is pinned through ``test_observations.py``'s shim at this
#: narrower family and carries three bare ``GI`` citations. Widening the family
#: under that file, which this casting may not edit, would turn its green pin
#: red for prose nobody had a chance to qualify. So the family is a scan
#: PARAMETER, exactly as ``bare_ok`` already is: one grammar, and each pin
#: declares the data it applies to.
LEGACY_ID_FAMILIES = frozenset({"AC", "OT", "FR", "CT", "ST", "NFR"})  # 6 items

#: The three legal qualifications. Order is irrelevant; membership is the whole
#: contract. A fourth spelling is a convention change, not a local decision.
#:
#: `fallout ` joined them for the foundry-run-fallout release. The tuple pins
#: every requirement-id citation under `tests/` to a spec NAME, and this run's
#: teammates qualify theirs `fallout FR-NNN`; without the member every casting
#: of the run fails the pin for writing the citation the convention asks for.
QUALIFIERS = ("process-fixes ", "convergence ", "fallout ")

#: Which spec each qualification names. Derived from `QUALIFIERS` at import so
#: the refusal below cannot advertise a narrower set than the scan enforces --
#: the `_PYTEST_DISCOVERY_PHRASE` shape, applied to the one message a teammate
#: reads when this pin refuses. A qualification with no row here is a bug in
#: this table, not a licence to print two of three.
QUALIFIER_SPECS = {
    "process-fixes ": "forge-specs/foundry-run-process-fixes/spec.md",
    "convergence ": "forge-specs/foundry-run-convergence/spec.md",
    "fallout ": "forge-specs/foundry-run-fallout/spec.md",
}

#: The accepted spellings, one per line, as the refusal prints them.
QUALIFIER_PHRASE = "\n".join(
    f"    {q.strip()!r:<22} -> {QUALIFIER_SPECS[q]}" for q in QUALIFIERS
)

#: The three `/`-joined runs `CHAIN_PHRASE` names, held as data so the test can
#: drive the EXACT strings the refusal prints rather than a paraphrase of them.
CHAIN_COVERED = "convergence CT-002 / AC-019 / OT-008"
CHAIN_BROKEN = "fallout AC-022 / C-081 / GI-002"
CHAIN_REPAIRED = "fallout AC-022 / GI-002 / C-081"

#: What a `/`-joined run does and does not carry, as the refusal prints it.
#:
#: fallout NFR-011 (D-174). The sentence this replaces said only that a run
#: "inherits its head's qualification" and illustrated it with `CHAIN_COVERED`
#: -- three requirement ids, which is the ONE shape that cannot exhibit the
#: constraint, because it is true of itself whatever the reader believes about
#: why. `_chain_pattern` strips only `<REQUIREMENT-ID> / ` segments, so a
#: run-local id ends the inheritance where it stands; the reader who learned
#: "the head qualifies the run" therefore wrote a run whose head WAS qualified
#: and whose middle was run-local, and it was refused. It defeated the author
#: of the offending line and the lead diagnosing it, which is why the fix is
#: this sentence rather than the regex: widening the chain to step over
#: run-local segments would let a qualification travel further than any pin
#: asks it to.
#:
#: Derived from `ID_FAMILIES` rather than typed beside it -- the
#: `QUALIFIER_PHRASE` shape one axis over -- and both runs it names are driven
#: by `test_a_run_local_id_breaks_the_chain_however_the_run_began`, so the run
#: this phrase calls broken has to be one that actually breaks.
CHAIN_PHRASE = (
    "A `/`-joined run inherits its head's qualification only THROUGH "
    "REQUIREMENT IDS: every segment between the qualification and the id must "
    "itself be one of "
    + ", ".join(f"{family}-NNN" for family in sorted(ID_FAMILIES))
    + f", so one prefix covers '{CHAIN_COVERED}'. Anything else in the run -- a "
    "concern id, a defect id -- ends the inheritance where it stands, and every "
    "requirement id AFTER it is reported however the run began: "
    f"'{CHAIN_BROKEN}' is qualified at its head and still reports its last id. "
    "Put the requirement ids first and the run-local ids last, "
    f"'{CHAIN_REPAIRED}', or give each id its own qualification."
)

PIN_SENTINEL = "# D-178 — THE TWO-SPEC ID CONVENTION IS PINNED, NOT MERELY DOCUMENTED."


@functools.lru_cache(maxsize=None)
def id_pattern(families: frozenset[str]) -> re.Pattern[str]:
    """The requirement-id regex over ``families``.

    Built rather than typed so the family set and the pattern cannot disagree.
    A hand-typed enum drifting away from the runtime guard standing beside it
    is a defect this repo has already paid for once, in ``server.py``, where
    the advertised tool schema and the handler's own checks came apart.
    """
    alt = "|".join(sorted(families))
    return re.compile(rf"\b(?:{alt})-\d{{3}}\b")


@functools.lru_cache(maxsize=None)
def _chain_pattern(families: frozenset[str]) -> re.Pattern[str]:
    """A ``/``-joined continuation: an ``XX-NNN / `` segment sitting between a
    qualification and the id it still governs.
    """
    alt = "|".join(sorted(families))
    return re.compile(rf"(?:{alt})-\d{{3}} / $")


def unqualified_ids(
    text: str,
    *,
    bare_ok: frozenset[str] = frozenset(),
    families: frozenset[str] = ID_FAMILIES,
) -> list[str]:
    """Every requirement id in ``text`` that names neither spec.

    ``text`` is one prose block with its line wrapping already collapsed, so a
    qualification and the id it governs are adjacent however the source broke
    the line.

    ``bare_ok`` is the per-module set of ids that module is permitted to cite
    bare. Under the total convention it is empty and every id carries its spec;
    it is non-empty only for the two modules pinned before the convention
    became total, which declare their bare ids rather than defaulting.
    """
    ids = id_pattern(families)
    chain = _chain_pattern(families)
    found: list[str] = []
    for match in ids.finditer(text):
        prefix = text[: match.start()]
        while True:
            stripped = chain.sub("", prefix)
            if stripped == prefix:
                break
            prefix = stripped
        if any(prefix.endswith(q) for q in QUALIFIERS):
            continue
        if match.group(0) in bare_ok:
            continue
        window = text[max(0, match.start() - 60) : match.end() + 20]
        found.append(f"{match.group(0)} in ...{window}...")
    return found


def prose_blocks(source: str, *, require_sentinel: bool = False) -> list[tuple[int, str]]:
    """``(lineno, normalised text)`` for every docstring and comment block.

    THE MODULE DOCSTRING IS SCANNED. It was not, before this cycle, and that
    exemption is where D-185 lived: ``tests/test_symbol_cites.py`` line 1
    carried the mis-attributing tag and no scan could have seen it. The
    exemption now keys off ``PIN_SENTINEL``: a module that carries the sentinel
    is one that EXPLAINS the collision, so its docstring and the sentinel's own
    comment block are exempt -- they have to be free to name both sides. Every
    other module's docstring is prose like any other.

    ``require_sentinel`` is the deletion guard for the two modules that carry
    the sentinel deliberately: if it goes missing there, the pin was disabled by
    deletion rather than by a visible edit, and this raises instead of quietly
    scanning more than it used to.
    """
    tree = ast.parse(source)
    blocks: list[tuple[int, str]] = []

    module_docstring_line = None
    if tree.body and isinstance(tree.body[0], ast.Expr):
        first = tree.body[0].value
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            module_docstring_line = first.lineno
            blocks.append((first.lineno, " ".join(first.value.split())))

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if not node.body or not isinstance(node.body[0], ast.Expr):
            continue
        value = node.body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            blocks.append((value.lineno, " ".join(value.value.split())))

    # Comments, grouped into contiguous runs so a wrapped comment paragraph is
    # one block rather than N unrelated lines.
    comments: list[tuple[int, str]] = []
    readline = io.StringIO(source).readline
    for token in tokenize.generate_tokens(readline):
        if token.type == tokenize.COMMENT:
            comments.append((token.start[0], token.string.lstrip("#").strip()))

    run_start: int | None = None
    run_text: list[str] = []
    prev_line = -10
    for lineno, text in comments + [(10**9, "")]:
        if lineno != prev_line + 1:
            if run_start is not None:
                blocks.append((run_start, " ".join(" ".join(run_text).split())))
            run_start, run_text = lineno, []
        run_text.append(text)
        prev_line = lineno

    pin_line = next(
        (i + 1 for i, line in enumerate(source.split("\n")) if line == PIN_SENTINEL),
        None,
    )
    if require_sentinel:
        assert pin_line is not None, (
            "the D-178 sentinel comment is gone; either it was renamed (restore it) "
            "or this pin is being disabled by deletion"
        )

    exempt: set[int | None] = set()
    if pin_line is not None:
        exempt.add(module_docstring_line)
        exempt.add(max((start for start, _ in blocks if start <= pin_line), default=pin_line))

    return sorted(
        ((start, text) for start, text in blocks if start not in exempt),
        key=lambda pair: pair[0],
    )


def assertion_messages(source: str) -> list[tuple[int, str]]:
    """``(lineno, normalised text)`` for every failure MESSAGE ``source`` raises.

    fallout NFR-011 (D-195, concern C-105) — THE PROSE SURFACE `prose_blocks`
    CANNOT SEE, AND IT IS THE ONE A FAILING ENGINEER ACTUALLY READS.

    `prose_blocks` walks docstrings and comments. An `assert`'s message operand
    is neither, so every requirement id written into one was invisible to the
    roster pin above — and modules NOT on any waiver were reported clean while
    carrying the exact defect this module exists to refuse. D-195 is that
    defect: `tests/test_skill_prose.py` is held to the full convention, passed
    every run, and carried a tier-contract id with no spec in front of it, in a
    message. Casting 11 measured the
    blind spot at 209 bare ids in assertion messages across `tests/` and closed
    it on its own module; this is the DETECTOR, so the next one is caught
    wherever it is written.

    SCOPED TO THE MESSAGE OPERAND, and the scoping is the convention's own. This
    module's legend exempts ids in CODE — a `spec_ref=` fixture literal, a
    `parametrize` entry, a module constant, an assertion's EXPECTED string —
    because those are input handed to a door, not a claim about which
    requirement a test proves. `assert lhs == rhs, "msg"` puts those two things
    on either side of one comma: `rhs` is data and stays unscanned, `msg` is
    prose addressed to a reader and is scanned like any other.

    f-strings and implicit concatenation are flattened to one line, so a cite
    split across source lines is still seen — the same normalisation
    `prose_blocks` applies, for the same reason.
    """
    tree = ast.parse(source)
    messages: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert) or node.msg is None:
            continue
        parts = [
            piece.value
            for piece in ast.walk(node.msg)
            if isinstance(piece, ast.Constant) and isinstance(piece.value, str)
        ]
        if parts:
            messages.append((node.lineno, " ".join(" ".join(parts).split())))
    return messages


def module_roster() -> list[Path]:
    """Every Python module in this directory TREE, test or not.

    ``conftest.py`` and ``__init__.py`` are in scope and are clean today; a
    citation written into a fixture helper is a citation like any other.

    fallout FR-005: `rglob`, not `glob`. The carve put fifteen modules under
    `tests/orchestration/`, and a roster derived one directory deep would have
    stopped judging every one of them on the day they were written — the silent
    half of a derived pin, which this file exists to prevent one axis over.
    """
    return sorted(
        p for p in TESTS_DIR.rglob("*.py") if "__pycache__" not in p.parts
    )


def module_key(path: Path) -> str:
    """A module's name in this roster: its path RELATIVE to `tests/`.

    fallout FR-005: `tests/test_spend.py` and `tests/orchestration/test_spend.py`
    are two modules with one basename, and a roster keyed on the basename would
    let one module's waiver silently cover the other module's prose.
    """
    return path.relative_to(TESTS_DIR).as_posix()


# --------------------------------------------------------------------------- #
# D-178 — THE TWO-SPEC ID CONVENTION IS PINNED, NOT MERELY DOCUMENTED.
#
# THE HISTORY, because this is the fourth attempt and the first three all had
# the same shape.
#
#   D-178 (cycle 10) — tests/test_observations.py tagged a test with a bare
#   pair of ids that resolve in both specs. Closed by a pin scoped to that ONE
#   file.
#   D-181 (cycle 11) — the identical defect in tests/test_escalation.py. Closed
#   by a second pin in that file, which IMPORTED casting 2's scan rather than
#   copying it. Better, and still one file at a time.
#   D-185 (cycle 12) — the identical defect in tests/test_symbol_cites.py,
#   whose module docstring reads "Casting 3 -- symbol-anchored cites (US-002 /
#   FR-004)". Both ids resolve, under the convergence spec, to requirements
#   about evidence tiers, which that file does not touch.
#
# WHY THE PER-FILE PINS DID NOT HOLD. Two reasons, and neither is effort.
#
#   1. THE CONVENTION WAS A DEFAULT, NOT A QUALIFICATION. "A bare id cites
#      convergence" is a rule each file declared for itself in its own
#      docstring. test_symbol_cites.py's ids are process-fixes ids -- its
#      subject IS the earlier spec's symbol-cite work -- so under a per-file
#      default it was never wrong locally and always wrong globally. Worse, a
#      DUAL-CITED id could not be pinned at all: tests/test_escalation.py says
#      so in its own comment, that FR-006, ST-001 and ST-002 appear there in
#      both senses and its guard cannot hold either. A default cannot express
#      "this occurrence means the other spec". A qualification can, which is
#      why the convention is now total: `process-fixes X` and `convergence X`,
#      no bare form, no default to be locally right about.
#   2. THE SCAN COULD NOT SEE THE FAMILY THE DEFECT WAS FILED IN. The regex
#      matched six families and D-185's tag leads with `US-002`. Widened here
#      to all eight the two specs share.
#
# WHY A ROSTER PIN AND NOT A FOURTH FILE-SCOPED ONE. A pin that covers the file
# it was filed against covers exactly the instance and teaches nothing about
# the next module. The roster below is derived from the directory, so a NEW
# test module is in scope the moment it exists -- nobody has to remember to add
# it -- and UNQUALIFIED_MODULES is the explicit, checked-in debt for the
# modules not yet converted, refused the moment an entry stops being needed.
#
# WHAT THIS PIN STILL CANNOT SEE, stated so nobody discovers it the hard way.
# A qualification asserts which spec an id names; it cannot assert that the id
# names the RIGHT requirement within that spec. `convergence AC-006` on a test
# that proves something else is a citation this scan calls well-formed. That
# check is a stream's to make against the spec text, and it is exactly the
# check D-185 came from -- what the convention buys is that the stream now
# knows which document to open.
# --------------------------------------------------------------------------- #

#: Modules whose prose still cites requirement ids bare. Each entry is standing
#: debt, not an exemption on the merits: the module predates the total
#: convention and nobody has qualified its citations yet.
#:
#: Kept honest from BOTH sides. An entry for a module that has become clean is
#: refused below, so the list cannot grow into a blanket waiver -- and a module
#: NOT listed here is held to the full convention, so a newly written bare id
#: in a clean module fails immediately.
#:
#: ONE ENTRY IS NOT UNCONVERTED WORK BUT AN EXPRESSIVE GAP IN THIS CONVENTION,
#: and it is the interesting one. ``test_foundry_init.py`` is casting 2's own.
#: Nine of its citations sit in the ``--url`` threading section at the top of
#: that file, and they name a THIRD spec, one that is not installed under
#: forge-specs/ at all -- checked both ways: no spec there carries a
#: ``target_url`` requirement, and neither installed spec's text for those ids
#: resembles what those tests actually prove. Both spellings offered above
#: would therefore be FALSE of them, and a confidently wrong citation is worse
#: than a bare one -- which is the whole lesson of D-185, so paying for the
#: green node with a fabricated qualification is the one thing this must not
#: do. Covering them needs a third form for ids whose spec is retired; that is
#: a convention change, not one file's decision, and it is recorded in
#: foundry-archive/daring-orca/concerns.md for the lead to route. (The ids are
#: not listed here for the same reason: naming them in prose would demand a
#: qualification of THIS file that no true one exists for.) Every other entry
#: is another casting's module and is ordinary unconverted debt.
#:
#: fallout FR-005: three of the carved modules came out CLEAN and are not
#: waived — `orchestration/test_evidence_boundary.py`, `orchestration/test_halt.py`
#: and `orchestration/test_tasks_codispatch.py` carry only prose this casting
#: wrote, qualified `fallout <ID>`, and the pin holds them directly. The other
#: thirteen carry inherited prose and inherit the waiver with it.
#: fallout FR-005 — THE CARVE'S FIFTEEN, INHERITED FROM ONE WAIVER.
#:
#: `test_orchestrator_gates.py` carried this waiver and was carved into
#: `tests/orchestration/`. Its prose travelled VERBATIM, so the debt travelled
#: with it: fifteen modules now hold what one module held, and requalifying it
#: is a pass over 17,000 lines of inherited comment that no casting of this run
#: was scoped to make. The waiver is inherited rather than granted — every
#: citation THIS casting writes into those modules is qualified `fallout <ID>`,
#: and the entry above says the same thing the deleted one did.
_CARVED_FROM_THE_ORCHESTRATOR_TEST = frozenset({
    "orchestration/_env.py",
    "orchestration/test_directives.py",
    "orchestration/test_escalation.py",
    "orchestration/test_fix_gate.py",
    "orchestration/test_gates.py",
    "orchestration/test_guidance.py",
    "orchestration/test_module_boundaries.py",
    "orchestration/test_report_seal.py",
    "orchestration/test_spend.py",
    "orchestration/test_streams.py",
    "orchestration/test_teams.py",
    "orchestration/test_transitions.py",
    "orchestration/test_width.py",
})

UNQUALIFIED_MODULES = _CARVED_FROM_THE_ORCHESTRATOR_TEST | frozenset(
    {
        "test_agent_frontmatter_parse.py",
        "test_foundry_init.py",
        "test_commit_guard.py",
        "test_escalation.py",
        "test_evidence.py",
        "test_findings_schemas.py",
        "test_fix_gate.py",
        "test_foundry_validate_key_links.py",
        "test_inspect_mode.py",
        "test_intent_coverage.py",
        "test_lead_prose.py",
        "test_liveness.py",
        "test_measure_run.py",
        "test_migrate_archive.py",
        "test_model_config.py",
        "test_nyquist_flag.py",
        "test_protocol_prose.py",
        "test_release_version.py",
        "test_report.py",
        "test_spawn_progress.py",
        "test_spend.py",
        "test_stream_rollup.py",
        "test_test_observations_validator.py",
        "test_validate_report.py",
        "test_vocab.py",
    }
)  # 26 items


#: fallout NFR-011 (D-195, concern C-105) — THE MESSAGE-SURFACE DEBT, PER ID
#: AND MEASURED.
#:
#: `{module: the ids it may still cite bare IN AN ASSERTION MESSAGE}`. The new
#: surface is being scanned for the first time, and the eleven ids below sat in
#: five modules that were already passing this pin — nobody wrote them in
#: defiance of a rule, the rule could not see them. Every one belongs to another
#: casting, so requalifying them here would be an edit to somebody else's tree.
#:
#: PER ID, NOT PER MODULE, and that is the difference between this and a second
#: `UNQUALIFIED_MODULES`. A module-level waiver would go on excusing every id
#: written into that module afterwards; these rows name the exact eleven, so the
#: TWELFTH fails the moment it is written. `unqualified_ids` already takes the
#: `bare_ok` parameter this feeds — one grammar, and each pin declares the data
#: it applies to, exactly as `LEGACY_ID_FAMILIES` does one axis over.
#:
#: SELF-EXPIRING. `test_no_message_waiver_outlives_the_debt_it_records` fails on
#: a row whose module no longer cites it bare, so an id cannot be qualified and
#: leave its waiver behind. That failure is the handshake by which a module
#: leaves this table, and it names the edit.
#:
#: NOT A PLACE TO PUT NEW DEBT. A module that is not here must be clean on this
#: surface, and adding a row is a convention change: qualify the id instead.
MESSAGE_BARE_OK: dict[str, frozenset[str]] = {
    "test_artifacts.py": frozenset({"GI-033"}),
    "test_evidence_for.py": frozenset({"US-008", "FR-051"}),
    "test_foundry_state_readers.py": frozenset({"FR-019", "NFR-005", "GI-033"}),
    "test_handoff_records.py": frozenset({"US-003", "NFR-002", "AC-015"}),
    "test_validate_ownership.py": frozenset({"FR-009"}),
}  # 5 modules, 11 ids


def _offences(path: Path) -> list[str]:
    """Every unqualified id in one module's prose, as reportable lines."""
    source = path.read_text(encoding="utf-8")
    return [
        f"line {lineno}: {offence}"
        for lineno, text in prose_blocks(source)
        for offence in unqualified_ids(text)
    ]


def _message_offences(path: Path) -> list[str]:
    """Every unqualified id in one module's assertion MESSAGES.

    Honours that module's `MESSAGE_BARE_OK` row, so the eleven ids the surface
    was carrying when it started being scanned are declared rather than
    defaulted — and any twelfth is reported.
    """
    source = path.read_text(encoding="utf-8")
    bare_ok = MESSAGE_BARE_OK.get(module_key(path), frozenset())
    return [
        f"line {lineno}: {offence}"
        for lineno, text in assertion_messages(source)
        for offence in unqualified_ids(text, bare_ok=bare_ok)
    ]


@pytest.mark.parametrize("module", [module_key(p) for p in module_roster()])
def test_every_requirement_id_in_this_module_names_its_spec(module: str) -> None:
    """The convention, applied to the whole directory rather than one file.

    D-178, D-181 and D-185 are the same defect in three different modules, and
    each was closed where it was found. This is the check those three were
    instances of.
    """
    if module in UNQUALIFIED_MODULES:
        pytest.skip(f"{module} is declared unqualified; see UNQUALIFIED_MODULES")

    offenders = _offences(TESTS_DIR / module)
    assert not offenders, (
        f"tests/{module}: unqualified requirement id(s) -- D-178/D-181/D-185 "
        f"again, in a module this pin holds to the full convention.\n"
        f"THE EDIT: in tests/{module}, at each line below, put a spec in front "
        f"of the id. Exactly {len(QUALIFIERS)} spellings are accepted:\n"
        f"{QUALIFIER_PHRASE}\n"
        f"There is no bare form and no per-file default; open the specs and "
        f"cite the one the surrounding prose actually describes.\n"
        f"{CHAIN_PHRASE}\n"
        f"{len(offenders)} unqualified id(s) in tests/{module}:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("module", [module_key(p) for p in module_roster()])
def test_every_requirement_id_in_this_modules_messages_names_its_spec(
    module: str,
) -> None:
    """fallout NFR-011 (D-195, concern C-105) — the same convention, on the
    surface the roster pin above cannot see.

    D-195 was a tier-contract id with no spec in front of it, in an assertion
    message of a module this file held to the FULL convention and reported clean
    every run, because `prose_blocks`
    walks docstrings and comments and a message is neither. The id resolved to
    the convergence spec's tier contract under one spec and to `Foundry-Concern`
    — a tool with no tiers and no filing doors — under the one this run builds
    against, which is the whole harm the convention exists to prevent.

    Casting 11 closed it on its own module and measured the blind spot; this is
    the detector, so the twelfth instance fails where it is written rather than
    where somebody happens to read it.

    THE WAIVER IS THE SAME ONE, because a module excused from the convention is
    excused on both surfaces. `UNQUALIFIED_MODULES` names the thirteen carved
    from the orchestrator test plus thirteen more whose inherited prose no
    casting of this run was scoped to requalify; those modules hold 195 of the
    206 bare ids on this surface, and scanning them here would demand the same
    out-of-scope sweep under a second test name.
    """
    if module in UNQUALIFIED_MODULES:
        pytest.skip(f"{module} is declared unqualified; see UNQUALIFIED_MODULES")

    offenders = _message_offences(TESTS_DIR / module)
    assert not offenders, (
        f"tests/{module}: unqualified requirement id(s) in an ASSERTION "
        f"MESSAGE -- D-195 again, in a module this pin holds to the full "
        f"convention.\n"
        f"A message is prose: it is the sentence a failing engineer reads, and "
        f"a bare id in it resolves to a different row under each installed "
        f"spec. It is exempt from the roster pin only because that pin walks "
        f"docstrings and comments, which is the blind spot this test closes.\n"
        f"THE EDIT: in tests/{module}, at each line below, put a spec in front "
        f"of the id. Exactly {len(QUALIFIERS)} spellings are accepted:\n"
        f"{QUALIFIER_PHRASE}\n"
        f"An assertion's EXPECTED string is still code and is still unscanned; "
        f"only the message operand after the comma is prose.\n"
        f"{CHAIN_PHRASE}\n"
        f"{len(offenders)} unqualified id(s) in tests/{module}:\n  "
        + "\n  ".join(offenders)
    )


def test_no_message_waiver_outlives_the_debt_it_records() -> None:
    """fallout NFR-011 (C-105) — `MESSAGE_BARE_OK`, checked from the other side.

    The cheapest way to make the pin above go green is to record the id, so the
    table is read back: a row whose module no longer cites it bare is a waiver
    for nothing, and a row naming a module that is gone is worse. Both fail
    here, which is what makes the table a debt ledger that expires rather than a
    second allow-list — the distinction C-054 and D-193 each deleted a table
    over, one guard along.

    This is also the handshake by which a module leaves the table: qualify the
    id, and this test names the row to delete.
    """
    present = {module_key(p) for p in module_roster()}

    missing = sorted(set(MESSAGE_BARE_OK) - present)
    assert not missing, (
        f"MESSAGE_BARE_OK names {missing}, which do not exist in {TESTS_DIR}.\n"
        "THE EDIT: delete "
        + ", ".join(repr(name) for name in missing)
        + " from MESSAGE_BARE_OK in tests/test_spec_id_convention.py. The "
        "module is gone and the row is a waiver for nothing."
    )

    # An id is still owed only while the module still cites it bare. Asked by
    # re-scanning with an EMPTY `bare_ok` and keeping the ids that come back:
    # deriving the answer from the same grammar the pin enforces, rather than
    # from a second reading of the source.
    retired: dict[str, list[str]] = {}
    for name, recorded in sorted(MESSAGE_BARE_OK.items()):
        source = (TESTS_DIR / name).read_text(encoding="utf-8")
        still_bare = {
            offence.split(" in ", 1)[0]
            for _, text in assertion_messages(source)
            for offence in unqualified_ids(text)
        }
        gone = sorted(recorded - still_bare)
        if gone:
            retired[name] = gone

    assert not retired, (
        f"MESSAGE_BARE_OK records id(s) the module no longer cites bare: "
        f"{retired}. Somebody qualified them, which is the point.\n"
        "THE EDIT: delete each id listed above from its row in "
        "MESSAGE_BARE_OK in tests/test_spec_id_convention.py, and the row "
        "itself once it is empty. This is not a failure in the module you just "
        "fixed: it is the handshake by which an id leaves the table, and once "
        "the row is gone the pin above holds that message directly."
    )

    # ...and the table has not become a place to put NEW debt. Every recorded id
    # is one this surface was already carrying when it started being scanned;
    # the count is named so a row added later is a visible edit rather than an
    # arithmetic nobody notices.
    assert sum(len(ids) for ids in MESSAGE_BARE_OK.values()) <= 11, {
        name: sorted(ids) for name, ids in sorted(MESSAGE_BARE_OK.items())
    }


def test_no_allow_list_entry_outlives_the_debt_it_records() -> None:
    """An entry for a clean module is a waiver nothing needs.

    The cheapest way to make the pin above go green is to add a name to
    UNQUALIFIED_MODULES, so the list is checked from the other side: an entry
    whose module no longer cites a bare id -- or which names no module at all
    -- fails here and must be deleted. That is also the handshake by which a
    module leaves the list: qualify it, and this test tells you to remove it.
    """
    present = {module_key(p) for p in module_roster()}

    missing = sorted(UNQUALIFIED_MODULES - present)
    assert not missing, (
        f"UNQUALIFIED_MODULES names {missing}, which do not exist in {TESTS_DIR}.\n"
        "THE EDIT: delete "
        + ", ".join(repr(name) for name in missing)
        + " from UNQUALIFIED_MODULES in tests/test_spec_id_convention.py. The "
        "module is gone and the entry is a waiver for nothing."
    )

    # fallout NFR-011 (C-105) — BOTH SURFACES, because the waiver covers both.
    # Asking this over prose alone would tell a module that is clean in its
    # docstrings and still carrying bare ids in its assertion messages to leave
    # the list, and the message pin above would then hold it to a convention
    # nobody had a chance to apply. A module leaves the waiver when it is clean
    # everywhere the waiver excused it.
    already_clean = sorted(
        name
        for name in UNQUALIFIED_MODULES
        if not _offences(TESTS_DIR / name) and not _message_offences(TESTS_DIR / name)
    )
    assert not already_clean, (
        f"UNQUALIFIED_MODULES declares {already_clean}, which cite no bare id "
        "any more, in prose OR in an assertion message -- somebody qualified "
        "them, which is the point.\n"
        "THE EDIT: delete "
        + ", ".join(repr(name) for name in already_clean)
        + " from UNQUALIFIED_MODULES in tests/test_spec_id_convention.py, and "
        "nothing else. This is not a failure in the module you just fixed: it "
        "is the handshake by which a module leaves the list, and once the "
        "entry is gone the parametrised pin above holds that module directly."
    )


def test_the_roster_is_derived_from_the_directory_not_typed() -> None:
    """A new test module is in scope the moment it exists.

    The three per-file pins each covered the file they were filed against. The
    roster is read off the directory so that nobody has to remember to add the
    next one, and so a module cannot leave the check by being forgotten.
    """
    roster = {module_key(p) for p in module_roster()}

    # This module, the three the defect chain ran through, and the two
    # non-test modules that are in scope and clean.
    for expected in (
        "test_spec_id_convention.py",
        "test_observations.py",
        "test_escalation.py",
        "test_symbol_cites.py",
        "conftest.py",
        "__init__.py",
    ):
        assert expected in roster, expected

    # fallout FR-005: the WHOLE TREE, so the carved `tests/orchestration/`
    # modules are in scope the moment they exist. A roster one directory deep
    # would have stopped judging fifteen modules on the day they were written,
    # which is the silent failure this file exists to prevent one axis over.
    assert roster == {
        module_key(p) for p in TESTS_DIR.rglob("*.py")
        if "__pycache__" not in p.parts
    }
    assert "orchestration/test_module_boundaries.py" in roster, sorted(roster)


def test_the_pin_reports_the_three_tags_it_was_written_for() -> None:
    """The pin's own fail-safe: a guard that cannot fail guards nothing.

    Driven directly over the prose D-178, D-181 and D-185 were each filed
    against, which must be reported, and over each legal form, which must not
    be. Every string below is CODE -- input to the scan, not a citation -- which
    is why none of them is qualified.
    """
    # D-178's tag, from tests/test_observations.py.
    assert unqualified_ids("AC-001 / OT-001 comment-prose filed as a defect")
    # D-181's tag, from tests/test_escalation.py.
    assert unqualified_ids(
        "AC-008 verbatim: 'After a GRIND->INSPECT transition, the server-side "
        "cycle counter has incremented without any caller-supplied value.'"
    )
    # D-185's tag, from tests/test_symbol_cites.py line 1 -- and the half of it
    # the old six-family regex could not see is the half that leads.
    assert unqualified_ids("Casting 3 -- symbol-anchored cites (US-002 / FR-004).")
    assert unqualified_ids("Casting 3 -- symbol-anchored cites (US-002).")

    # A chain whose head is unqualified is not rescued by its own tail.
    assert unqualified_ids("CT-002 / AC-019 / OT-008 — accepts PARTIAL")

    # Both qualifications are legal, across a `/` chain and across a line wrap.
    assert not unqualified_ids("process-fixes AC-001 / OT-001 is refused")
    assert not unqualified_ids("convergence AC-001 / OT-001 is refused")
    assert not unqualified_ids("process-fixes CT-002 / AC-019 / OT-008 — PARTIAL")
    assert not unqualified_ids("convergence US-002 / FR-004 — symbol-anchored")
    assert not unqualified_ids("evaded process-fixes ST-002 escalation while")
    assert not unqualified_ids("evaded convergence ST-002 escalation while")


def test_a_run_local_id_breaks_the_chain_however_the_run_began() -> None:
    """fallout NFR-011 -- the refusal's own examples, driven.

    The sentence `CHAIN_PHRASE` replaces illustrated the chain rule with three
    requirement ids, which is the one shape that cannot exhibit it: such a run
    inherits whatever the reader believes about why. So a reader learned "the
    head qualifies the run", wrote a run whose head was qualified and whose
    middle was run-local, and was refused by the sentence that taught them.
    An example that cannot fail teaches nothing, and this drives the examples
    the refusal actually prints so a run it calls broken is one that breaks.

    Every id below is CODE -- input to the scan, not a citation -- which is why
    none of the strings carries a qualification of this module's own.
    """
    # The covered run: a qualification, then requirement ids the whole way.
    assert not unqualified_ids(CHAIN_COVERED)

    # The broken run, and it is the line the replaced sentence denied: a
    # properly qualified HEAD does not rescue what sits behind a run-local
    # segment. Asserted as the exact reported list, not merely as truthy, so
    # the phrase cannot start naming a run that fails for a different reason.
    assert unqualified_ids(CHAIN_BROKEN) == [f"GI-002 in ...{CHAIN_BROKEN}..."]

    # The repair the phrase prescribes, driven rather than claimed.
    assert not unqualified_ids(CHAIN_REPAIRED)

    # POSITION IS THE WHOLE RULE, and it is position-independent in both
    # directions: a run-local segment at the front breaks everything after it,
    # and one at the back breaks nothing, because nothing after it is scanned.
    assert unqualified_ids("fallout C-081 / D-170 / GI-002")
    assert not unqualified_ids("fallout GI-002 / C-081 / D-170")
    assert not unqualified_ids("fallout GI-002 / AC-022 / C-081")

    # And the phrase states exactly that, with the family set derived rather
    # than typed beside it -- so a family added to `ID_FAMILIES` widens the
    # sentence a teammate reads instead of leaving it a member short.
    for family in ID_FAMILIES:
        assert f"{family}-NNN" in CHAIN_PHRASE, family
    for run in (CHAIN_COVERED, CHAIN_BROKEN, CHAIN_REPAIRED):
        assert run in CHAIN_PHRASE, run


def test_a_dual_cited_id_is_pinnable_now_that_the_convention_is_total() -> None:
    """The limitation the per-file pins could not lift, lifted.

    tests/test_escalation.py states in its own comment that three of the ids it
    cites appear there in BOTH senses, that declaring one waives the check for
    every occurrence of it in that file, and that a scan able to hold them
    "would have to demand a qualification on EVERY id, convergence ones
    included". That is what this scan does, so the two senses of one id are now
    distinguishable and the bare form is reported in either. The three are
    named in code below rather than here, because naming one in prose would
    force a qualification onto an id whose whole point is that it has two.
    """
    for dual in ("FR-006", "ST-001", "ST-002"):
        assert unqualified_ids(f"{dual} says CONSECUTIVE")
        assert not unqualified_ids(f"process-fixes {dual} says CONSECUTIVE")
        assert not unqualified_ids(f"convergence {dual} says CONSECUTIVE")


def test_the_widened_family_is_the_half_of_d185_the_old_scan_missed() -> None:
    """US and GI were invisible, and D-185's tag leads with a US id.

    Asserted as a difference between the two declared families rather than as a
    bare claim, so the reason LEGACY_ID_FAMILIES still exists is visible: it is
    what tests/test_escalation.py is pinned at through the shim, and widening it
    under that file would turn its green pin red for three GI citations this
    casting may not edit.
    """
    legacy = {"families": LEGACY_ID_FAMILIES}
    assert not unqualified_ids("US-002 symbol-anchored cites", **legacy)
    assert not unqualified_ids("GI-006 requires the generated report", **legacy)

    assert unqualified_ids("US-002 symbol-anchored cites")
    assert unqualified_ids("GI-006 requires the generated report")

    # And the cost of the narrow family, asserted rather than assumed: an
    # invisible id cannot head a `/` chain, so the tail of one is reported on
    # its own. That is why tests/test_escalation.py declares convergence
    # CT-014 -- not because it cites it bare by choice, but because the id in
    # front of it is a family this pin cannot see.
    assert unqualified_ids("GI-006 / CT-014: DONE", **legacy) == [
        "CT-014 in ...GI-006 / CT-014: DONE..."
    ]
    assert not unqualified_ids("convergence GI-006 / CT-014: DONE")

    assert LEGACY_ID_FAMILIES < ID_FAMILIES
    assert ID_FAMILIES - LEGACY_ID_FAMILIES == {"US", "GI"}


def test_ids_in_code_are_never_citations() -> None:
    """A `spec_ref=` literal is input to a door, not a claim about a test.

    Scanning code would force a qualification into fixture data that the server
    then stores verbatim, which would corrupt the record to satisfy a lint.
    """
    source = '\n'.join(
        [
            '"""convergence AC-006 -- the module legend."""',
            'SPEC_REF = "AC-006"',
            'def t():',
            '    """convergence AC-006 -- what this proves."""',
            '    assert file_defect(spec_ref="FR-004")["spec_ref"] == "FR-004"',
        ]
    )
    joined = " ".join(text for _, text in prose_blocks(source))
    assert "the module legend" in joined
    assert "what this proves" in joined
    assert not [o for _, t in prose_blocks(source) for o in unqualified_ids(t)]

    # ...and the message scan draws the line in the same place: the EXPECTED
    # string on the left of the comma stays code, so the module above carries no
    # message offence at all despite the two unqualified `spec_ref=` literals in
    # it -- the assertion there has no message operand for either to sit in.
    assert not [o for _, t in assertion_messages(source) for o in unqualified_ids(t)]


def test_an_assertion_message_is_prose_and_its_expected_string_is_not() -> None:
    """fallout NFR-011 (D-195, C-105) — the anchor for the new surface.

    The recogniser is driven over a module built to carry the D-195 shape: a
    bare id in a message, another inside an f-string, a THIRD in the expected
    string beside it, and a docstring that names none of them. A scan over the
    clean tree would be green whether it worked or not, and the whole failure
    being closed is a surface nobody was looking at.
    """
    source = "\n".join(
        [
            '"""convergence AC-006 -- the module legend."""',
            "def t():",
            '    assert door(spec_ref="FR-004")["tier"] == "FR-004", (',
            '        "the reproduction rung CT-001 states"',
            "    )",
            "def u(n):",
            '    assert n, f"the {n} span AC-015 reports"',
            "def v():",
            '    assert ok, "fallout GI-033 is qualified and is not reported"',
        ]
    )

    # `prose_blocks` sees the docstring and NOTHING else, which is why the two
    # bare ids below survived every run of the roster pin.
    assert not [o for _, t in prose_blocks(source) for o in unqualified_ids(t)]

    found = [o.split(" in ", 1)[0] for _, t in assertion_messages(source) for o in unqualified_ids(t)]
    assert found == ["CT-001", "AC-015"], found

    # The expected string is code: the `spec_ref=` literal and the value it is
    # compared against sit on the other side of the comma and are never
    # reported, however many times they appear.
    assert "FR-004" not in found
    # ...and a qualified id in a message is not reported either, so the pin
    # refuses the bare form rather than the surface.
    assert "GI-033" not in found

    # The waiver parameter reaches this surface through the same grammar.
    waived = [
        o.split(" in ", 1)[0]
        for _, t in assertion_messages(source)
        for o in unqualified_ids(t, bare_ok=frozenset({"CT-001"}))
    ]
    assert waived == ["AC-015"], waived


def test_a_module_docstring_is_scanned_unless_it_carries_the_sentinel() -> None:
    """D-185 lived in a module docstring, which the old scan never read.

    The exemption is not a list of filenames -- it keys off the sentinel, so a
    module that explains the collision may name both sides of it and every
    other module's docstring is prose like any other.
    """
    plain = '"""Casting 3 -- symbol-anchored cites (US-002 / FR-004)."""\n'
    assert [o for _, t in prose_blocks(plain) for o in unqualified_ids(t)]

    legend = plain + f"\n{PIN_SENTINEL}\n# and its rationale, free to name both.\n"
    assert not [o for _, t in prose_blocks(legend) for o in unqualified_ids(t)]

    # The deletion guard: a module pinned at the sentinel that loses it raises
    # rather than silently widening what it scans.
    with pytest.raises(AssertionError, match="sentinel"):
        prose_blocks(plain, require_sentinel=True)
    prose_blocks(legend, require_sentinel=True)


def test_every_qualification_names_the_spec_it_cites() -> None:
    """fallout AC-013's shape, applied to this module's own refusal.

    ``QUALIFIER_PHRASE`` is what a teammate reads when the pin refuses, and it
    is BUILT from ``QUALIFIERS`` rather than typed beside it -- the
    ``_PYTEST_DISCOVERY_PHRASE`` pattern. This asserts the two halves cannot
    come apart: a qualification added to the tuple with no row in
    ``QUALIFIER_SPECS`` would raise at import, and one added to the table with
    no place in the tuple would advertise a spelling the scan rejects.
    """
    assert set(QUALIFIER_SPECS) == set(QUALIFIERS)
    for qualification, spec in QUALIFIER_SPECS.items():
        assert qualification.endswith(" "), qualification
        assert spec.startswith("forge-specs/"), spec
        assert qualification.strip() in QUALIFIER_PHRASE
        assert spec in QUALIFIER_PHRASE
