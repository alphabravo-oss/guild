"""The INSPECT width decision and its readers.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import json
from pathlib import Path



from foundry_mcp.tools import artifacts

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.
from foundry_mcp.tools.orchestration import width as _width

# fallout AC-014 — THE TWO SIBLING SUITES THE CARVE MUST KEEP REACHING.
#
# `test_observations` owns the never-demote corpus and `test_spawn_progress`
# owns the shipped-source-tree derivation. Both are imported rather than copied,
# for the reason the monolith imported them: a parity test that owned its own
# copy of the corpus would keep passing while the two corpora drifted, and two
# scans over "the shipped source" must not be able to disagree about what that
# is. If either renames a symbol the ImportError says so by name, which is the
# loud failure rather than the silent one.

# fallout FR-004 / AC-014 (D-183) — THE ROSTER AND ITS HELPERS COME FROM
# `tests/orchestration/_env.py`, WHICH IS THE ONE PLACE THEY ARE STATED.
#
# This module carried its own byte-identical copy of a hand-typed thirteen-tuple
# and of `orchestration_source`, `owning_module`, `patch_everywhere` and
# `orchestration_has`. Fourteen copies of one roster is fourteen places to
# forget a module, and `keyfiles.py` — shipped in cycle 5 — was forgotten in
# every one of them: `owning_module` answered the IMPORTING module for
# `covers_path` and raised for `owning_entries`, and `patch_everywhere` could
# not reach a binding inside it. The roster is derived from the package
# directory now, so there is one of it and it cannot go stale.

from tests.orchestration._env import (  # noqa: F401
    ORCHESTRATION,
    _write_manifest_with_castings,
    _write_state,
    run_env,
)

from foundry_mcp.tools.orchestration.width import (  # noqa: F401
    _trace_skip_from_width,
)

# fallout GI-008 / GI-009 / GI-033 (D-021 / D-035, ruling item 5) — the fence
# split in two. `width._trace_skip_from_width` DECIDES at the transition and the
# answer is recorded in the `inspect_modes` entry; `guidance._stamp_trace_skip`
# READS the recorded answer and performs the stamp. Both halves are driven
# below, and the entries the fixtures write now carry the field a real decision
# would have written.
from foundry_mcp.tools.orchestration.guidance import (  # noqa: F401
    _stamp_trace_skip,
)








def test_the_trace_skip_fires_on_a_delta_cycle_with_an_empty_diff(run_env):
    """...and is not removed, which would be GI-007 from the other side.

    In DELTA mode TRACE's scope is "the symbols the GRIND commits touched"
    (AC-019). An empty diff has no symbols to walk, which is the one case where
    skipping and running are the same answer.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [], "touched_files": [],
        "trace_skip": {"skip": True, "reason": "DELTA width and the GRIND diff is empty — there are no touched symbols for TRACE to walk"},
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    # The DECISION, at the transition that records the width...
    assert _trace_skip_from_width(False, "delta", [], "DELTA")["skip"] is True
    # ...and the EFFECT, at the surface that reads what was recorded.
    decision = _stamp_trace_skip(fdir)

    assert decision["skip"] is True, decision
    assert (fdir / ".trace-complete").exists()




def test_the_trace_skip_does_not_fire_on_a_delta_cycle_with_a_diff(run_env):
    """The other DELTA arm: files were touched, so there are symbols to walk."""
    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2, inspect_modes=[{
        "cycle": 2, "phase": "F2", "mode": "DELTA", "rule": "delta",
        "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"],
        "stream_scope": {}, "prove_sample": [],
        "touched_files": ["src/api/a.py"],
        "trace_skip": {"skip": False, "reason": "DELTA width over 1 touched file(s) — TRACE runs over the symbols the GRIND commits touched"},
    }])
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)

    assert _trace_skip_from_width(False, "delta", ["src/api/a.py"], "DELTA")["skip"] is False
    decision = _stamp_trace_skip(fdir)

    assert decision["skip"] is False, decision
    assert not (fdir / ".trace-complete").exists()


# --------------------------------------------------------------------------- #
# fallout OT-013 / OT-014 / FR-043 — ONLY THE VERIFIER FORCES FULL WIDTH.
# --------------------------------------------------------------------------- #

#: The one spelling of where this casting's modules live, so the roster rows
#: below and the derivation in
#: `test_every_shipped_orchestration_module_is_classified_by_one_roster` cannot
#: disagree about what a package module's path looks like.
_ORCHESTRATION = (
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/"
)

#: The surfaces a GRIND may touch and still earn a DELTA INSPECT. Every module
#: row is one this casting created, and the point of creating them was that the
#: monolith made this list impossible: one file held the gate ladder AND the
#: report seal AND the spend ledger, so a diff touching the spend door forced a
#: FULL cycle over everything.
#:
#: EVERY delta-side orchestration module, not a sample of them (concern C-094).
#: This roster and its sibling below are a claim about the WHOLE package — each
#: module either forces FULL or deliberately does not — and it named five of the
#: nine that deliberately do not. `fix_gate.py`, `guidance.py`, `keyfiles.py`
#: and `streams.py` sat on neither side, so no test asked whether a diff
#: touching them earns the DELTA cycle the split exists to buy. Coverage is
#: derived below now; what is typed here is only the CLASSIFICATION.
_DELTA_SURFACES = (
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/display.py",
    f"{_ORCHESTRATION}report_seal.py",
    f"{_ORCHESTRATION}spend.py",
    f"{_ORCHESTRATION}halt.py",
    # should-not-stop's park door: a lifecycle writer of the parked state.
    f"{_ORCHESTRATION}park.py",
    f"{_ORCHESTRATION}directives.py",
    f"{_ORCHESTRATION}teams.py",
    f"{_ORCHESTRATION}fix_gate.py",
    f"{_ORCHESTRATION}guidance.py",
    f"{_ORCHESTRATION}keyfiles.py",
    f"{_ORCHESTRATION}streams.py",
    "plugins/foundry/commands/start.md",
    "plugins/foundry/agents/teammate.md",
    "plugins/foundry/references/lead-discipline.md",
)

#: The surfaces whose diff makes a verdict already reached UNTRUSTWORTHY, which
#: is the only thing `verifier_touched` is for.
#:
#: `escalation.py` is the FIFTH DECIDING MODULE (concern C-094).
#: `VERIFIER_PATH_PATTERNS` names five orchestration modules and this roster
#: named four, so a diff touching the module whose rung readers decide which
#: class is ESCALATED forced FULL with nothing here driving it — and the
#: emptiness guard that was supposed to catch a too-narrow roster counted
#: instead of deriving, so it passed on the wrong shape.
_VERIFIER_SURFACES = (
    f"{_ORCHESTRATION}gates.py",
    f"{_ORCHESTRATION}transitions.py",
    f"{_ORCHESTRATION}width.py",
    f"{_ORCHESTRATION}evidence_boundary.py",
    f"{_ORCHESTRATION}escalation.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py",
    "plugins/foundry/mcp-server/src/foundry_mcp/schemas/findings.py",
)


def test_the_split_surfaces_that_may_earn_a_delta_inspect():
    """fallout OT-013 — a diff touching only these records rule `delta`.

    This is what the whole carve BUYS. Before it, one file held the gate ladder,
    the report seal, the spend ledger, the halt door, the directives and the team
    lifecycle, so a diff touching any of them matched the verifier pattern and
    every GRIND ran at FULL width. Casting 10 narrowed `VERIFIER_PATH_PATTERNS`
    to four module paths; this asserts the paths it names are the ones that now
    exist, which is the half a narrowing cannot check for itself.
    """
    from foundry_mcp.schemas.vocab import is_verifier_path

    forced = [p for p in _DELTA_SURFACES if is_verifier_path(p, None)]
    assert forced == [], (
        f"{forced} still match the verifier pattern, so a GRIND touching only "
        "the presentation and lifecycle surfaces would run at FULL width — the "
        "cost the split exists to remove."
    )


def test_every_verifier_surface_still_forces_full_after_the_split():
    """fallout OT-014 / FR-043 — and the narrowing did not narrow too far.

    A pattern set that matched NOTHING would satisfy the test above and be
    catastrophic: `verifier_touched` would never fire and a run that rewrote its
    own gate would re-use verdicts the rewrite invalidated. Both halves, on the
    same list, in the same commit.
    """
    from foundry_mcp.schemas.vocab import is_verifier_path

    missed = [p for p in _VERIFIER_SURFACES if not is_verifier_path(p, None)]
    assert missed == [], (
        f"{missed} no longer match the verifier pattern. A diff moving any of "
        "them can make a verdict already reached wrong, which is the whole of "
        "what `verifier_touched` is for."
    )


def test_the_two_sets_are_disjoint_and_the_underivable_rows_are_anchored():
    """The emptiness guard on both axes at once — ANCHORED, NOT COUNTED.

    concern C-094 — A COUNT PASSES ON ANY ROSTER OF THE RIGHT SIZE.
    -------------------------------------------------------------
    This read `len(_DELTA_SURFACES) >= 6 and len(_VERIFIER_SURFACES) >= 6`, and
    both rosters were the wrong SHAPE while it was green: `escalation.py` was
    absent from the verifier side and `fix_gate.py`, `guidance.py`,
    `keyfiles.py` and `streams.py` from the delta side. A floor cannot tell a
    roster that is small from one that is wrong, which is the failure D-226
    named in its own domain and D-183 named in this one.

    The package half is derived by the test below. What is anchored HERE is the
    half no glob over `tools/orchestration/` will ever reach: the leaf module,
    the evidence corpus, the schemas and the loaded prose. Those are named
    individually rather than counted, because a named row says which surface
    went missing and a number says only that one did.
    """
    assert set(_DELTA_SURFACES).isdisjoint(_VERIFIER_SURFACES)

    for anchor in (
        # Renders refusals, never defines them — the distinction that keeps a
        # presentation module off the verifier side.
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/display.py",
        # The three loaded-prose surfaces A-005 puts on the delta side.
        "plugins/foundry/commands/start.md",
        "plugins/foundry/agents/teammate.md",
        "plugins/foundry/references/lead-discipline.md",
    ):
        assert anchor in _DELTA_SURFACES, anchor

    for anchor in (
        # The module that re-executes the evidence corpus decides whether a log
        # passes (GI-006).
        "plugins/foundry/mcp-server/src/foundry_mcp/tools/evidence.py",
        # The closed vocabularies and finding shapes every stream validates on.
        "plugins/foundry/mcp-server/src/foundry_mcp/schemas/vocab.py",
        "plugins/foundry/mcp-server/src/foundry_mcp/schemas/findings.py",
    ):
        assert anchor in _VERIFIER_SURFACES, anchor


def test_every_shipped_orchestration_module_is_classified_by_one_roster():
    """concern C-094 (fallout D-183) — A SHIPPED MODULE CANNOT GO UNDRIVEN.

    The two rosters above are hand-typed, and the PAIR of them is a claim about
    the whole package: every orchestration module either forces FULL or
    deliberately does not, and both halves are driven by the tests above.
    Nothing asserted the pair covered what SHIPS, so the package could outgrow
    them silently — and had. `escalation.py` matched `VERIFIER_PATH_PATTERNS`
    with no row driving it; `fix_gate.py`, `guidance.py`, `keyfiles.py` and
    `streams.py` sat on neither side; and the only guard on the pair counted
    rows instead of comparing them to the package.

    Derived membership is what makes that lag impossible rather than merely
    recorded — a module that ships without a row here fails on the commit that
    ships it, not a wave later. `ORCHESTRATION` is the directory imported (see
    `tests/orchestration/_env.py`), and `test_module_boundaries.py` pins that
    roster equal to its own independent glob, so this walks what ships rather
    than what someone remembered.

    THE SPLIT IS `is_verifier_path`'S AND NOT THIS MODULE'S. A roster that
    asserted its own classification would pass on any self-consistent pair,
    which is exactly how a roster drifts from the rule it samples without
    anything going red. So membership is derived and classification is typed,
    and each is checked against the other.

    ONE-DIRECTIONAL BY DESIGN: the rosters also carry the leaf, the schemas and
    the loaded prose, which are not package modules and must not be demanded
    here. `test_the_two_sets_are_disjoint_and_the_underivable_rows_are_anchored`
    is that half.
    """
    from foundry_mcp.schemas.vocab import is_verifier_path

    shipped = {
        f"{_ORCHESTRATION}{Path(module.__file__).name}"
        for module in ORCHESTRATION
    }
    assert shipped, "the orchestration package is empty; this claim covers nothing"

    verifier = set(_VERIFIER_SURFACES)
    delta = set(_DELTA_SURFACES)
    assert verifier.isdisjoint(delta), sorted(verifier & delta)
    assert not shipped - (verifier | delta), {
        "shipped but driven by neither roster": sorted(shipped - (verifier | delta)),
    }

    # Both directions against the RULE, so a row on the WRONG side is as loud as
    # a row that is missing.
    assert not [p for p in sorted(shipped & verifier) if not is_verifier_path(p, None)]
    assert not [p for p in sorted(shipped & delta) if is_verifier_path(p, None)]




# --------------------------------------------------------------------------- #
# fallout research/holmes-orchestrator.md#acc-3 (D-097) — the research-skip
# record's accepted spellings are declared once and honoured as declared.
# --------------------------------------------------------------------------- #


def test_the_research_skip_read_honours_exactly_its_declared_spellings(run_env):
    """fallout research/holmes-orchestrator.md#acc-3 / RA-6 (D-097).

    RA-6 names `_research_skipped` reading three uncoordinated storage locations
    as a cohesion defect. The accepted set is now DECLARED — the marker plus
    `_RESEARCH_SKIPPED_KEY` in each of `_RESEARCH_SKIPPED_DOCUMENTS` — instead of
    being spelled inline in a loop, so the set a reader must satisfy is stated in
    one place rather than discovered by reading the reader.

    THIS IS THE PIN ON THE DECLARATION. It drives every declared spelling in
    isolation and asserts each one alone is enough, and it drives an UNdeclared
    document to assert the read is not simply scanning everything. A spelling
    silently dropped from the declaration fails here, and a fourth location
    added to the reader without joining the declaration fails here too.

    WHAT IS NOT CLOSED, recorded so nobody reads this pin as saying it is: no
    shipped writer writes ANY of the three (swept across `plugins/foundry/**`
    for both spellings — the only writes are test fixtures), so the single WRITE
    point RA-6 also asks for belongs at `foundry_init` in casting 4's
    `tools/foundry.py` and is raised as a cross-casting concern.
    """
    _project_root, fdir = run_env

    documents = _width._RESEARCH_SKIPPED_DOCUMENTS
    key = _width._RESEARCH_SKIPPED_KEY
    assert documents, "the accepted-document set is empty; the read is blind"
    assert key == "research_skipped", key

    def _clear() -> None:
        (fdir / artifacts.RESEARCH_SKIPPED_MARKER).unlink(missing_ok=True)
        for document in documents:
            path = fdir / document
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({}), encoding="utf-8")

    # Nothing recorded anywhere: the read says no.
    _clear()
    assert _width._research_skipped(fdir) is False

    # The marker alone.
    _clear()
    (fdir / artifacts.RESEARCH_SKIPPED_MARKER).write_text("x\n", encoding="utf-8")
    assert _width._research_skipped(fdir) is True, "the marker spelling stopped counting"

    # Each declared document alone — driven per member, so a spelling dropped
    # from the tuple is named by the parametrised failure rather than hidden by
    # another member still answering yes.
    for document in documents:
        _clear()
        (fdir / document).write_text(json.dumps({key: True}), encoding="utf-8")
        assert _width._research_skipped(fdir) is True, document

    # ...and an UNdeclared document does not count, which is what makes the
    # declaration a set rather than a description of a scan.
    _clear()
    undeclared = fdir / "verdicts.json"
    undeclared.write_text(json.dumps({key: True}), encoding="utf-8")
    assert undeclared.name not in documents, undeclared.name
    assert _width._research_skipped(fdir) is False, (
        "the read counted a document the declaration does not name"
    )




# --------------------------------------------------------------------------- #
# fallout FR-030 (D-209, concern C-125, research/holmes-orchestrator.md#reg-3)
# — AN ENTRY IS NEVER DROPPED, AND THE EMPTY ENTRY IS READ.
#
# reg-3 is one sentence with two halves: "never drop a dispatch entry. When
# `handler.__code__` is None, OR THE WALK FINDS NO `foundry_mcp` MODULE FILE,
# record `registry[tool] = []` instead of `continue`/skip." Both were open and
# only the first looked like a skip. The three tests below are the two halves
# and the reason the sentence exists — an empty entry is a fact
# `_test01_scope_touched` acts on, and before C-125 nothing read it.
# --------------------------------------------------------------------------- #


_C125_CONTRACTS_TABLE = """
## Contracts

| ID     | surface | input | output | errors | citation |
|--------|---------|-------|--------|--------|----------|
| CT-014 | Foundry-Report (new tool) | run artifacts | REPORT.md | refusal | [from A-024] |
"""


class _NoCodeObject:
    """A handler with no `__code__` — what a callable object or a
    `functools.partial` bound into `_DISPATCH` actually looks like to the walk.
    """

    def __call__(self, args):  # pragma: no cover - never dispatched here
        return {}


def _registry_over(monkeypatch, **extra_handlers) -> dict:
    """`_registry_tool_modules()` with extra entries bound into `_DISPATCH`."""
    from foundry_mcp import server as _server

    monkeypatch.setattr(
        _server, "_DISPATCH", dict(_server._DISPATCH, **extra_handlers), raising=True
    )
    return _width._registry_tool_modules()


def test_the_registry_keeps_an_entry_whose_handler_has_no_code_object(monkeypatch):
    """reg-3's first half: `handler.__code__` is None.

    Driven before the fix: the entry was absent from the returned mapping
    entirely, so a Contracts row naming that tool matched nothing and the
    covered set narrowed with no one saying so.
    """
    registry = _registry_over(monkeypatch, **{"Zz-No-Code": _NoCodeObject()})

    assert "Zz-No-Code" in registry, sorted(registry)
    assert registry["Zz-No-Code"] == [], registry["Zz-No-Code"]


def test_the_registry_keeps_an_entry_whose_walk_resolves_no_module_file(monkeypatch):
    """reg-3's second half — the `if files:` that read as tidiness.

    A handler that names no `foundry_mcp` module resolves to no file. That is
    the SAME fact as the arm above (ownership is not derivable) and it was
    dropped by a different statement, which is why the filing naming only the
    first would have left the class open.
    """
    registry = _registry_over(monkeypatch, **{"Zz-Empty-Walk": lambda args: {"ok": True}})

    assert "Zz-Empty-Walk" in registry, sorted(registry)
    assert registry["Zz-Empty-Walk"] == [], registry["Zz-Empty-Walk"]


def test_every_dispatched_tool_has_a_registry_entry():
    """The property both halves add up to, stated over the real table.

    `set(registry) == set(server._DISPATCH)`. Casting 12 lands the same pin in
    `tests/test_inspect_mode.py`; this is the one that fails in the module that
    would have to change.
    """
    from foundry_mcp import server as _server

    registry = _width._registry_tool_modules()
    assert set(registry) == set(_server._DISPATCH), {
        "dropped": sorted(set(_server._DISPATCH) - set(registry)),
        "invented": sorted(set(registry) - set(_server._DISPATCH)),
    }


def test_no_dispatch_entry_hides_its_handler_behind_a_server_helper():
    """fallout D-223 / holmes#reg-2 — THE PROPERTY THAT LICENSES THE ONE-STEP WALK.

    `_registry_tool_modules` used to take one more hop into any function
    `server.py` itself defines, because two entries — `"Foundry-Liveness":
    lambda args: _dispatch_liveness(args)` and `"Foundry-Report": lambda args:
    _dispatch_report()` — named the real handler only INSIDE that helper, in a
    function-local import. Stopping at the lambda mapped CT-014 to `server.py`
    and left `tools/foundry_report.py` uncovered, which is half of D-204.

    Both adapters are gone and the hop went with them. What replaces the hop is
    this: the registrar's contract — "plain lambdas naming the handler GLOBAL" —
    is now ASSERTED rather than described, so a re-introduced server-side adapter
    fails here, naming the tool and the helper, instead of quietly degrading the
    registry's answer to `[]` and TEST-01's covered set with it.

    RED BEFORE THE FIX. At HEAD 55e3a0c this named exactly the two adapters:
    `{'Foundry-Liveness': ['_dispatch_liveness'], 'Foundry-Report':
    ['_dispatch_report']}`. It is the check the fix had to turn green, and it is
    what makes deleting the `module == "foundry_mcp.server"` branch a provable
    deletion rather than a claimed one.

    It is deliberately NOT an assertion that every entry resolves to something —
    `test_the_registry_keeps_an_entry_whose_walk_resolves_no_module_file` above
    is the record that an unresolvable entry is a legal, DECLARED state (D-209).
    This asserts the narrower thing the walk's shape depends on: whatever an
    entry names, it does not name a function of this file.
    """
    from types import FunctionType

    from foundry_mcp import server as _server

    offenders: dict[str, list[str]] = {}
    for tool, handler in _server._DISPATCH.items():
        code = getattr(handler, "__code__", None)
        if code is None:
            continue
        for name in code.co_names:
            if name.startswith("foundry_mcp"):
                continue
            obj = _server.__dict__.get(name)
            if not isinstance(obj, FunctionType):
                continue
            if (getattr(obj, "__module__", "") or "") == "foundry_mcp.server":
                offenders.setdefault(str(tool), []).append(name)

    assert offenders == {}, (
        f"dispatch entries reaching a function server.py defines: {offenders}. "
        f"Bind the handler global directly — `lambda args: handler(..., "
        f"project_root=_project_root)` — and put run-dir resolution, defaults "
        f"and refusals in the handler's own module. `_registry_tool_modules` "
        f"resolves an entry in ONE step, so a helper here makes the tool's "
        f"ownership unknown to `_test01_scope_touched`."
    )
    # The scan must SEE the table, or an empty `offenders` proves nothing.
    assert len(_server._DISPATCH) >= 20, len(_server._DISPATCH)


def test_an_unresolvable_registry_entry_makes_the_covered_set_unknown(
    run_env, monkeypatch
):
    """concern C-125 — the per-entry rung, which the empty list exists FOR.

    `_test01_scope_touched` failed closed on an empty WHOLE registry and had no
    rung for one unresolvable ENTRY. Casting 12 drove it with `Foundry-Report`
    re-spelled as a partial and the diff touching the very file CT-014 is
    implemented by:

        BASELINE:  {'touched': True,  'computable': True, ...names CT-014}
        PARTIAL:   {'touched': False, 'computable': True, 'detail': ''}

    The second is a claim about a covered set that was never computed. It is
    D-207's own sentence — "no evidence is not evidence of absence" — at
    per-entry granularity.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, self_target=True)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    (fdir / "spec.md").write_text(
        "- **FR-001**: the thing works\n" + _C125_CONTRACTS_TABLE, encoding="utf-8"
    )
    report_module = str(
        Path(artifacts.__file__).resolve().parent / "foundry_report.py"
    )
    touched = [report_module]

    # (1) THE BASELINE. `Foundry-Report` resolves, the diff touched what
    # implements it, and the answer names the row.
    resolvable = _width._test01_scope_touched(fdir, project_root, touched)
    assert resolvable["touched"] is True, resolvable
    assert resolvable["computable"] is True, resolvable
    assert "CT-014" in resolvable["detail"], resolvable

    # (2) THE SAME RUN with that ONE entry unresolvable. Before C-125 this was
    # `touched: False, computable: True` — narrower AND reported as computed.
    # The real answer captured BEFORE the patch: patching a name and then
    # calling it through the patched module is a call to the patch.
    real = _width._registry_tool_modules()
    monkeypatch.setattr(
        _width,
        "_registry_tool_modules",
        lambda: dict(real, **{"Foundry-Report": []}),
        raising=True,
    )
    unresolvable = _width._test01_scope_touched(fdir, project_root, touched)
    assert unresolvable["computable"] is False, unresolvable
    assert unresolvable["touched"] is True, (
        "an unknown covered set requires the stream; anything else is the "
        "fail-open arm D-207 closed, reopened one entry at a time"
    )
    assert "CT-014" in unresolvable["detail"], unresolvable
    assert "Foundry-Report" in unresolvable["detail"], unresolvable


def test_a_resolvable_hit_still_wins_over_an_unresolvable_sibling_row(
    run_env, monkeypatch
):
    """C-125's ordering, which is not incidental.

    A row whose tool DID resolve to a touched file is answered: the covered set
    is known to intersect the diff, and an unresolvable sibling cannot unmake
    that. Only the NEGATIVE answer is the one an unresolved row makes unsayable.
    Without this the rung would widen every DELTA that happens to bind one
    partial, which is the over-correction the fail-open arm invites.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, self_target=True)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    (fdir / "spec.md").write_text(
        "- **FR-001**: the thing works\n"
        + _C125_CONTRACTS_TABLE.rstrip("\n")
        + "\n| CT-013 | Foundry-Spend (new tool) | agent | record | none | [from A-022] |\n",
        encoding="utf-8",
    )
    tools_dir = Path(artifacts.__file__).resolve().parent
    touched = [str(tools_dir / "foundry_report.py")]

    real = _width._registry_tool_modules()
    monkeypatch.setattr(
        _width,
        "_registry_tool_modules",
        lambda: dict(real, **{"Foundry-Spend": []}),
        raising=True,
    )
    decision = _width._test01_scope_touched(fdir, project_root, touched)
    assert decision["touched"] is True, decision
    assert decision["computable"] is True, decision
    assert "CT-014" in decision["detail"], decision


#: fallout FR-030 (D-221) — a Contracts table whose one row names NO dispatched
#: tool, so the registry rung is reached with `rows` non-empty and the positive
#: walk below it can never fire. That is the only input on which
#: `not any(registry.values())` and `not registry` give different answers.
_D221_CONTRACTS_TABLE = """
## Contracts

| ID     | surface | input | output | errors | citation |
|--------|---------|-------|--------|--------|----------|
| CT-099 | the archive writer | run artifacts | an archive | none | [from A-021] |
"""


def test_an_all_unresolvable_registry_cannot_answer_a_computed_miss(
    run_env, monkeypatch
):
    """fallout FR-030 (D-221) — the guard D-209 moved, with a check that can
    go red.

    D-209's remedy moved this arm's fail-open guard from `if not registry:` to
    `if not any(registry.values()):`, because `_registry_tool_modules` records
    an entry for every dispatched tool and leaves its file list EMPTY when
    ownership is not derivable — so the container is non-empty whenever
    `_DISPATCH` is, and the old spelling had stopped firing on exactly the tree
    it exists for. THAT MOVE REVERTED WITH THE WHOLE SUITE GREEN: the one-token
    revert `-    if not any(registry.values()):` / `+    if not registry:` gave
    5351 passed, 113 skipped, exit 0 at ac89f59, byte-for-byte the baseline.

    THE TWO EXISTING PINS CANNOT REACH IT, and that is not an oversight in
    them. `test_an_unresolvable_registry_entry_makes_the_covered_set_unknown`
    and `test_a_resolvable_hit_still_wins_over_an_unresolvable_sibling_row`
    both patch the registry to `dict(real, **{"<tool>": []})` — ONE empty entry
    among many non-empty ones — so `registry` and `any(registry.values())` are
    both truthy and the two spellings agree. The all-values-empty state is the
    only input that separates them, and nothing constructed it.

    DRIVEN, so this is the behavioural difference and not a re-reading of the
    source: at HEAD the answer is `{touched: True, computable: False, source:
    'unknown'}`; with the revert it is `{touched: False, computable: True,
    source: 'registry', detail: ''}` — a computed-miss claim over a registry
    that resolved nothing, which is D-207's fail-open shape reopened at the
    container level one cycle after C-125 closed it at the entry level. TEST-01
    drops off the roster and nobody is told.
    """
    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=1, self_target=True)
    _write_manifest_with_castings(fdir, ["src/api/a.py"], no_ui=True)
    (fdir / "spec.md").write_text(
        "- **FR-001**: the thing works\n" + _D221_CONTRACTS_TABLE,
        encoding="utf-8",
    )
    # Not a `schemas/` path and not a declared scope, so the ladder reaches the
    # registry rung rather than being answered above it.
    touched = [str(Path(artifacts.__file__).resolve().parent / "foundry_report.py")]

    real = _width._registry_tool_modules()
    # THE STATE D-209's REMEDY EXISTS TO REPRESENT: every entry present, every
    # one unresolvable. Captured from the real registry before patching, so the
    # key set is the shipped one rather than a hand list that could go empty.
    assert real, "the registry is empty; this pin would be measuring nothing"
    monkeypatch.setattr(
        _width,
        "_registry_tool_modules",
        lambda: {tool: [] for tool in real},
        raising=True,
    )

    decision = _width._test01_scope_touched(fdir, project_root, touched)
    assert decision["computable"] is False, decision
    assert decision["touched"] is True, (
        "an unknown covered set REQUIRES the stream; anything else is the "
        "fail-open arm D-207 closed, reopened at the container level"
    )
    assert decision["source"] == "unknown", decision
    assert decision["detail"], decision

    # ...and the SAME run with the real registry answers a computed miss, which
    # is what makes the assertion above about the emptiness of the values and
    # not about the table. Without this the pin would pass on any input that
    # happened to reach `unknown` by another arm.
    monkeypatch.setattr(
        _width, "_registry_tool_modules", lambda: real, raising=True
    )
    computed = _width._test01_scope_touched(fdir, project_root, touched)
    assert computed["computable"] is True, computed
    assert computed["touched"] is False, computed
    assert computed["source"] == "registry", computed


def test_the_scope_match_drops_a_dot_prefix_and_keeps_a_dotted_name(run_env):
    """fallout research/holmes-orchestrator.md#share-11 (D-233).

    `_path_matches` folded both sides with `.lstrip("./")`, which strips any
    run of `.` and `/` CHARACTERS rather than a `./` prefix: a dot-directory
    scope entry stopped matching the file it names, and a dotted name matched
    the undotted one. Driven through the declared-scope arm of
    `_test01_scope_touched`, the production caller, on the research's own
    probes plus `.hidden` and `../a.py`.
    """
    project_root, fdir = run_env
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    def touched(scope: str, candidate: str) -> bool:
        (fdir / "castings" / "manifest.json").write_text(
            json.dumps({"castings": [], "test01_scope": [scope]}), encoding="utf-8"
        )
        answer = _width._test01_scope_touched(fdir, project_root, [candidate])
        assert answer["source"] == "declared", answer
        return answer["touched"]

    # A dot-directory entry matches the file it names, under any root.
    assert touched(".claude/agents/x.md", "plugins/foundry/.claude/agents/x.md") is True
    # The `./` PREFIX is still dropped.
    assert touched("./src/a.py", "src/a.py") is True
    # A leading dot is part of the name, and `..` is not a prefix to discard.
    for scope, candidate in (
        ("github/w.yml", ".github/w.yml"),
        ("claude/x", ".claude/x"),
        ("hidden", ".hidden"),
        ("src/a.py", "../a.py"),
    ):
        assert touched(scope, candidate) is False, (scope, candidate)
