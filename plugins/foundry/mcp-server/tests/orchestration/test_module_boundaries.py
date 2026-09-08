"""The package-wide guards: layering, single definition, and the AST pins.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import ast
import re
import asyncio
import builtins
import importlib
import inspect
import io
import json
import os
import subprocess
import sys
import textwrap
import tokenize
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import foundry_mcp
from foundry_mcp.schemas import vocab
from foundry_mcp.schemas.vocab import (  # noqa: F401
    DEFECT_TIERS,
    RUN_PHASE_HALTED,
)
from foundry_mcp.tools import artifacts, foundry_state
from foundry_mcp.tools.foundry_state import (  # noqa: F401
    current_cycle,
    now_iso,
    overlay_unreported,
)
from foundry_mcp.tools.display import format_result

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.
from foundry_mcp.tools.orchestration import gates as _gates
from foundry_mcp.tools.orchestration import keyfiles as _keyfiles
from foundry_mcp.tools.orchestration import streams as _streams
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
from tests.test_spawn_progress import _scanned_modules, _scanned_roots  # noqa: F401

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
from tests.orchestration._env import ORCHESTRATION, orchestration_has, orchestration_source, owning_module  # noqa: F401

from tests.orchestration._env import (  # noqa: F401
    _arm_ordering_token,
    _defect_ledger,
    _halted_run,
    _tiered,
    _write_manifest_with_castings,
    _write_spec,
    _write_state,
    _write_verdicts,
    run_env,
)

from foundry_mcp.tools.orchestration.gates import (  # noqa: F401
    GATE_TO_TRANSITION,
    foundry_gate,
)

from foundry_mcp.tools.orchestration.transitions import (  # noqa: F401
    PHASE_TOKENS,
    _phase_transition,
    foundry_mark_phase_complete,
)

from tests.orchestration._env import (  # noqa: F401
    FIXTURE_CLASS,
    FIXTURE_TIER,
    GUARDED_CYCLE_READERS,
    _BACKTICK_CITE,
    _BAD_CONTAINERS,
    _BAD_UTF8_DOCUMENT,
    _BAD_UTF8_SPEC,
    _DECODE_RAISES,
    _DOCUMENT_LOADERS,
    _DOCUMENT_LOAD_RAISES,
    _FILE_READ_METHODS,
    _INSTALLED_DEPENDENCY_DIRS,
    _LEDGER_REFUSAL_DECORATOR,
    _LOADER_SEGMENTS,
    _OLD_ID_FAMILIES,
    _ORCHESTRATOR_LEDGER_DOORS,
    _PLANTED_ALIASED_LOADER,
    _PLANTED_COVERED_SPLIT_LOADER,
    _PLANTED_DECODE_ONLY_LOADER,
    _PLANTED_FROM_IMPORT_LOADER,
    _PLANTED_LEDGER_SCAN,
    _PLANTED_LOADER,
    _PLANTED_OSERROR_DECODE_LOADER,
    _PLANTED_RERAISING_LOADER,
    _PLANTED_SPLIT_LOADER,
    _PLANTED_WRITER,
    _PROSE_CITES_WITH_NO_DEFINITION,
    _READ_RAISES,
    _RENAME_PRIMITIVES,
    _populate_sweep_worktree,
    _widened_id_families,
    shipped_python_files,
)




# --------------------------------------------------------------------------- #
# D-047 / FR-004 — the MCP tool descriptions are a cite-policy surface
#
# server.py's Foundry-Accept-Casting description is delivered verbatim into
# every lead's context with the tool list, and it named only `file:line` — while
# agents/teammate.md asks for path#Symbol, the tool's OWN return payload names
# both, and tools/citation.py accepts both with the line component never judged.
# One gate, three descriptions, and the one a lead reads first and most often
# never mentioned the durable form. No test asserted on any MCP description
# string, which is why the D-040 cite-policy sweep did not reach this copy.
# --------------------------------------------------------------------------- #


def test_the_accept_casting_description_leads_with_the_durable_cite_form():
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    accept = next(t for t in tools if t.name == "Foundry-Accept-Casting")

    assert "path#Symbol" in accept.description
    # And it leads: the durable form is named before the legacy one, so a lead
    # skimming the sentence reads the form the protocol actually wants.
    assert accept.description.index("path#Symbol") < accept.description.index("file:line")
    # The legacy form is still named as accepted, because the implementation
    # still accepts it — a description that dropped it would be the same defect
    # pointing the other way.
    assert "file:line" in accept.description
    assert "legacy" in accept.description




def test_the_accept_casting_description_accounts_for_every_hard_reject_branch():
    """D-077 / D-078 / FR-017 / AC-023 — the description ENUMERATED the gate's
    blocking conditions and the enumeration was false.

    It ended "Blocks acceptance if the teammate reported scope cuts OR any
    requirement has no citation." Two conditions named; the handler has nine
    hard-reject branches plus the warning-conditional tail, and the word
    "evidence" appeared nowhere in the string. The one blocking cause it denied
    existed — ``evidence_verdict == "rejected"`` — was at the time the MOST
    likely way the gate would block, because EVID-01 was rejecting this run's
    own evidence logs in a cold worktree. A lead who had not separately read
    commands/start.md learned nothing from the tool surface itself.

    ASSERTED AS A DERIVATION, not as a literal (D-079's lesson: a pin that
    quotes the prose it guards can be defeated by editing the prose). Every
    hard-reject branch is recovered from the handler's own AST by its guard
    expression, and each must appear in the roster below carrying the
    vocabulary the description owes it. A branch nobody rostered fails by name,
    which is the case this exists to catch: a tenth blocking condition added to
    the handler while the description still names five.
    """
    import ast

    from foundry_mcp import server as foundry_server

    # fallout AC-061 / FR-063 / GI-033 (D-192, concern C-107) — THE DOOR IS IN
    # `tools/evidence.py` NOW, AND THIS PIN FOLLOWS IT.
    #
    # `foundry_accept_casting` RUNS `verify_evidence`, so while it was defined
    # in `foundry_handoff.py` it was the one lifecycle-to-verifier crossing this
    # tree had. GI-033's arithmetic gave nothing to hoist — what was reached was
    # the engine, not a symbol — so C-107 reversed the edge instead and the
    # handler moved beside the engine. An `ast.parse` pin names a FILE, so it
    # does not follow a function object the way `inspect.getsource` does: this
    # is one of the four this casting repointed in the same dispatch (GI-026).
    door_path = Path(artifacts.__file__).parent / "evidence.py"
    source = door_path.read_text(encoding="utf-8")
    function = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "foundry_accept_casting"
    )

    def _is_hard_reject(stmt: ast.stmt, test: ast.expr) -> bool:
        """The gate refusing, not warning — in either of its two spellings.

        (1) `return {... "ok": False ...}` — a refusal built inline.

        (2) `if <name> is not None: return <name>` — a refusal a SHARED helper
            built and this handler forwards. Added with CT-011 / AC-030, which
            move the prompt-hash rung out of this handler and into
            `check_reported_prompt_hash` so Foundry-Fix consumes the same one.
            The dict-literal-only scanner went blind to that rung the moment it
            was shared, and a blocking condition invisible to this test is a
            blocking condition nobody has to roster or describe — which is the
            control failing quietly, in the exact direction the test exists to
            prevent. The detector is WIDENED rather than the roster trimmed.
        """
        if not isinstance(stmt, ast.Return):
            return False
        if isinstance(stmt.value, ast.Dict):
            for key, value in zip(stmt.value.keys, stmt.value.values):
                if isinstance(key, ast.Constant) and key.value == "ok":
                    return isinstance(value, ast.Constant) and value.value is False
            return False
        # The forwarded-refusal shape: the guard names the very thing returned.
        if isinstance(stmt.value, ast.Name):
            return stmt.value.id in {
                node.id for node in ast.walk(test) if isinstance(node, ast.Name)
            }
        return False

    # The guard expression is the identity: it IS the blocking condition, and
    # it survives rewording of the error text and every line-number shift.
    guards = {
        ast.get_source_segment(source, node.test)
        for node in ast.walk(function)
        if isinstance(node, ast.If)
        for stmt in node.body
        if _is_hard_reject(stmt, node.test)
    }

    # guard expression -> lowercase substrings the tool description owes it.
    # An empty tuple means the description covers the branch generically and
    # deliberately does not spend a lead's attention on it.
    owed = {
        'not fdir': (),
        'not spec_result.get("ok")': (),
        "spec_hash != current_spec_hash": ("spec_hash",),
        "not prompt_path.exists()": (),
        # CT-011 / AC-030 — the hash rung moved OUT of this handler and into the
        # shared `check_reported_prompt_hash`, which Foundry-Fix consumes too. A
        # hash check that exists at one door and not the other lets an unread
        # prompt through whichever door the lead happens to walk, so the guard
        # here is now "the shared helper refused" rather than an inline
        # comparison. The description still owes the word, because the refusal a
        # lead sees is still about prompt_hash.
        "hash_refusal is not None": ("prompt_hash",),
        # CT-015 / AC-015 — the new blocking condition. Unrostered, this test
        # fails by name, which is exactly the case it exists to catch.
        "not casting_commit": ("casting_commit",),
        "not match": ("<spec_requirements>",),
        "_read_spec_format_version(evidence_spec_path) is None": (
            "spec_format_version",
        ),
        'evidence_verdict == "rejected"': (
            "evidence re-execution rejected the casting",
        ),
        "unbound": ("bound to no evidence",),
    }

    assert guards == set(owed), (
        f"the hard-reject branches of foundry_accept_casting have changed.\n"
        f"  unrostered (in the code, not in this test): {sorted(guards - set(owed))}\n"
        f"  stale (in this test, not in the code):      {sorted(set(owed) - guards)}\n"
        f"Every blocking condition the handler has must be accounted for here, "
        f"and named in the Foundry-Accept-Casting description if a lead needs "
        f"it to diagnose a refusal. That description is delivered verbatim into "
        f"every lead's context and is what they read at the moment of the call."
    )

    tools = asyncio.run(foundry_server.list_tools())
    accept = next(t for t in tools if t.name == "Foundry-Accept-Casting")
    described = accept.description.lower()
    for guard, tokens in sorted(owed.items()):
        for token in tokens:
            assert token in described, (
                f"the Foundry-Accept-Casting description never says {token!r}, "
                f"so a lead cannot connect a refusal from `{guard}` to anything "
                f"the tool told them about."
            )

    # The scope-flag / citation / unresolved-cite branches do not return
    # `ok: False` directly — they set `warning`, and the tail return computes
    # `ok = warning is None`. They block acceptance all the same, so the
    # description owes them too.
    for token in ("scope cuts", "no citation", "resolves nowhere"):
        assert token in described, token




def test_the_accept_casting_description_says_what_omitting_the_sha_costs():
    """D-078, and CT-015's answer to it.

    Evidence re-execution was documented ONLY in the nested ``casting_commit``
    property description — which a lead composing the call from the headline has
    no reason to open — and omitting the SHA skipped BOTH EVID-01 and EVID-02
    while still returning ok:true. The failure mode was a green acceptance that
    verified nothing.

    The previous version of this test ended by noting that the handler's
    optional default "is what makes the silence possible; if that ever becomes
    required, this warning is the thing that must change with it." CT-015 made
    it required, so this is that change: the description must now say the
    omission is REFUSED, and the silent-skip warning must be gone — a
    description still warning about a silence that can no longer happen sends a
    lead hunting for a failure mode the gate has closed.
    """
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    accept = next(t for t in tools if t.name == "Foundry-Accept-Casting")
    described = accept.description

    # The parameter, the two checks it engages, and the mechanism.
    assert "casting_commit" in described
    assert "EVID-01" in described and "EVID-02" in described
    assert "re-execut" in described.lower()

    # ...and the cost of leaving it out, in the description itself rather than
    # only in the property below it. That cost is now a REFUSAL, and the
    # description says so.
    lowered = described.lower()
    assert "required" in lowered
    assert "refus" in lowered
    # The silent-skip warning is retired with the silence it described.
    assert "silently" not in lowered
    assert "ok:true" not in lowered.replace(" ", "")

    # The handler keeps an Optional default deliberately: it is what lets the
    # handler's OWN named refusal fire on an absent value instead of a
    # TypeError escaping across the MCP boundary. The obligation is enforced by
    # `required` above it and by that refusal, never by a signature that cannot
    # produce the house {error, hint} shape.
    import inspect

    # fallout AC-061 / GI-033 (D-192, concern C-107): the handler's home is
    # `tools/evidence.py`, beside the verification it runs.
    from foundry_mcp.tools import evidence as door_module

    params = inspect.signature(door_module.foundry_accept_casting).parameters
    assert params["casting_commit"].default is None
    assert "casting_commit" in accept.inputSchema["required"]




def test_the_accept_casting_description_agrees_with_the_handlers_own_payload():
    """The three copies must say one thing. This pins the tool description
    against the string the handler itself returns in ``must_verify``, so the two
    cannot drift apart again without a test failing."""
    from foundry_mcp import server as foundry_server

    # fallout AC-061 / GI-033 (D-192, concern C-107): repointed with the door.
    door_src = (
        Path(artifacts.__file__).parent / "evidence.py"
    ).read_text(encoding="utf-8")
    assert "path#Symbol or file:line citation" in door_src

    tools = asyncio.run(foundry_server.list_tools())
    accept = next(t for t in tools if t.name == "Foundry-Accept-Casting")
    for form in ("path#Symbol", "file:line"):
        assert form in accept.description




# --------------------------------------------------------------------------- #
# Registration halves owned by this casting (AC-023 / CT-004)
# --------------------------------------------------------------------------- #


def test_accept_casting_schema_carries_casting_commit():
    """AC-023 / FR-017: the handler has always accepted casting_commit and
    gates the whole evidence re-execution block on `is not None`, but the
    parameter had no schema property and no dispatch path — so over MCP it was
    ALWAYS None, nothing in tools/evidence.py ever ran from a real run, and
    manifest.evidence_provenance was never populated."""
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    accept = next(t for t in tools if t.name == "Foundry-Accept-Casting")

    props = accept.inputSchema["properties"]
    assert "casting_commit" in props
    assert props["casting_commit"]["type"] == "string"
    # CT-015 / AC-015 / FR-010: REQUIRED, not optional. Optional, it was ALWAYS
    # omitted — which is the whole of the defect this test was written for, one
    # step further on. A gate whose evidence re-execution is opt-in verified
    # nothing while returning ok:true, so the omission is now a refusal.
    assert "casting_commit" in accept.inputSchema["required"]




def test_accept_casting_dispatch_delivers_casting_commit_to_the_handler(monkeypatch):
    """The transport half of AC-023, asserted by DRIVING the dispatcher.

    This claim used to be checked by grepping the dispatch lambda's source text
    for an argument-passing expression, which proves nothing about where the
    value ends up: the string can be present while the argument is dropped, and
    absent while the wiring is correct. What matters is that a casting_commit
    handed to the tool by name ARRIVES at the handler's parameter — and that
    omitting it still yields None, which is the backwards-compatible path the
    evidence block keys on.
    """
    from foundry_mcp import server as foundry_server

    # fallout AC-061 / GI-033 (D-192, concern C-107): repointed with the door.
    from foundry_mcp.tools import evidence as door_module

    seen: list[dict] = []

    def _spy(**kwargs):
        seen.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(foundry_server, "foundry_accept_casting", _spy)

    base = {
        "casting_id": 2,
        "spec_hash": "abc",
        "prompt_hash": "def",
        "completion_report": "report",
    }
    foundry_server._DISPATCH["Foundry-Accept-Casting"]({**base, "casting_commit": "deadbeef"})
    foundry_server._DISPATCH["Foundry-Accept-Casting"](dict(base))

    assert seen[0]["casting_commit"] == "deadbeef"
    assert seen[1]["casting_commit"] is None

    # ...and the real handler genuinely has that parameter, so the transported
    # value lands somewhere rather than being swallowed by **kwargs.
    import inspect

    params = inspect.signature(door_module.foundry_accept_casting).parameters
    assert "casting_commit" in params
    assert params["casting_commit"].default is None




# --------------------------------------------------------------------------- #
# D-006 / D-007 — the Foundry-Phase enum and the handler cannot drift
# --------------------------------------------------------------------------- #


def _handler_phase_tokens() -> set[str]:
    """Every literal ``foundry_mark_phase_complete`` branches on.

    Read out of the function's own AST rather than from a list maintained
    beside it, so the guard below cannot be satisfied by updating a copy and
    forgetting the branch.
    """
    import ast
    import inspect
    import textwrap

    # D-067 moved the branch chain into `_phase_transition` so the ordering
    # token is consumed only by a transition that succeeded. The branches — and
    # therefore the accepted token set — live there now.
    tree = ast.parse(textwrap.dedent(inspect.getsource(_phase_transition)))
    tokens: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not (isinstance(node.left, ast.Name) and node.left.id == "phase"):
            continue
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and isinstance(comparator, ast.Constant):
                if isinstance(comparator.value, str):
                    tokens.add(comparator.value)
    return tokens




def test_phase_schema_enum_equals_the_handler_branch_set():
    """D-006 / D-007 / FR-005: the advertised enum and the implemented branches
    are the same set.

    The enum had drifted in BOTH directions at once. It omitted
    ``inspect_start`` — the only token whose branch increments the cycle
    counter — and the SDK validates arguments against the advertised enum
    BEFORE dispatch, so over MCP the counter could never leave 0 however the
    handler behaved. It also advertised research_done / decompose_done /
    validate_done, for which there is no branch at all: three tokens a lead
    could read off the tool list and never successfully call.
    """
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    phase_tool = next(t for t in tools if t.name == "Foundry-Phase")
    advertised = set(phase_tool.inputSchema["properties"]["phase"]["enum"])
    implemented = _handler_phase_tokens()

    assert advertised == implemented, {
        "advertised_but_unimplemented": sorted(advertised - implemented),
        "implemented_but_unadvertised": sorted(implemented - advertised),
    }
    # The handler's own declared roster is the third copy; it feeds the
    # else-branch refusal, so a drift there misnames the legal set.
    assert set(PHASE_TOKENS) == implemented
    assert len(PHASE_TOKENS) == len(set(PHASE_TOKENS))




def test_the_cycle_advancing_token_is_reachable_over_mcp(run_env):
    """D-006 stated as the behaviour it broke: ``inspect_start`` is advertised,
    and driving it through the dispatcher advances the counter."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F3", cycle=4)
    _arm_ordering_token(fdir)

    tools = asyncio.run(foundry_server.list_tools())
    phase_tool = next(t for t in tools if t.name == "Foundry-Phase")
    assert "inspect_start" in phase_tool.inputSchema["properties"]["phase"]["enum"]

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Phase"]({"phase": "inspect_start"})
    finally:
        foundry_server._project_root = previous_root

    assert result["cycle"] == 5, result
    assert json.loads((fdir / "state.json").read_text(encoding="utf-8"))["cycle"] == 5




# --------------------------------------------------------------------------- #
# D-008 / D-009 — the observation channel and the defect-filing fields exist
# over MCP, not only as Python functions
# --------------------------------------------------------------------------- #


def test_observation_tools_are_registered_and_reach_their_handlers(run_env):
    """AC-001 / FR-001 / FR-023: ``foundry_add_observation`` and
    ``foundry_query_observations`` existed with no Tool() declaration and no
    dispatch entry, so no MCP path could record or read an observation — the
    typed channel the defect/observation split routes to was unreachable from
    a real run, which leaves a stream with nowhere to put a comment-prose
    finding except the defect ledger."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=0)

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}
    assert "Foundry-Observation" in tools
    assert "Foundry-Observations" in tools
    assert set(tools["Foundry-Observation"].inputSchema["required"]) == {
        "cycle", "source", "description",
    }

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        filed = foundry_server._DISPATCH["Foundry-Observation"]({
            "cycle": 0,
            "source": "trace",
            "description": (
                "the comment above the loop says line 42 but the guard moved "
                "and the line number is now stale"
            ),
            "target_kind": "comment",
        })
        assert "error" not in filed, filed
        assert filed["observation_id"].startswith("O-")

        queried = foundry_server._DISPATCH["Foundry-Observations"]({})
    finally:
        foundry_server._project_root = previous_root

    assert [o["id"] for o in queried["observations"]] == [filed["observation_id"]]
    # Observations are their own ledger and are NEVER mixed into defects.
    assert not (fdir / "defects.json").exists()
    assert (fdir / "observations.json").exists()




def test_the_candidate_drive_door_is_registered_and_reaches_its_handler(run_env):
    """fallout ST-007 / GI-027 — the WRITE half, reachable over MCP.

    Concern C-004 from casting 4: `foundry_drive_temper_candidate` shipped in
    `tools/foundry.py` with its own regression tests, and the registration
    lands in `server.py`, which is this casting's file. A Python function with
    no `Tool()` declaration and no `_DISPATCH` entry is a channel a real run
    cannot reach, which is D-008/D-009 one layer up — and until it lands TEMPER
    has a roster it can read and no call that closes anything on it.

    The sibling pin above names its two tools explicitly, so it cannot see a
    third. This drives the round trip the concern asks for: open a candidate
    through the wire, close it through the wire, and read it back DRIVEN.

    ABSENCE TRAVELS AS ABSENCE (D-074). `filed` carries no schema `default`
    and the dispatch spells `args.get("filed", "")`, so an omitted field
    reaches the writer as the empty string it was given rather than as a
    manufactured id. Driven here by omitting it: clean is a closure.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F5", cycle=1)

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}
    assert "Foundry-Drive-Candidate" in tools, sorted(tools)
    schema = tools["Foundry-Drive-Candidate"].inputSchema
    assert set(schema["required"]) == {"observation_id"}, schema
    assert "default" not in schema["properties"]["filed"], schema["properties"]["filed"]

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        opened = foundry_server._DISPATCH["Foundry-Observation"]({
            "cycle": 1,
            "source": "prove",
            "description": (
                "probe idea: drive the halt door from a phase that has no "
                "entry-source row and see whether the seal still writes"
            ),
            "classification": "TEMPER_CANDIDATE",
            "target_kind": "comment",
        })
        assert "error" not in opened, opened

        # `filed` omitted entirely — the clean closure.
        driven = foundry_server._DISPATCH["Foundry-Drive-Candidate"]({
            "observation_id": opened["observation_id"],
        })
        assert "error" not in driven, driven
        assert driven["driven_finding"] == "", driven

        read_back = foundry_server._DISPATCH["Foundry-Observations"](
            {"classification": "TEMPER_CANDIDATE"}
        )
    finally:
        foundry_server._project_root = previous_root

    closed = [
        o for o in read_back["observations"]
        if o["id"] == opened["observation_id"]
    ]
    assert len(closed) == 1, read_back
    assert closed[0].get("status") == "DRIVEN", closed[0]




def test_an_absent_target_kind_is_refused_at_the_mcp_boundary_too(run_env):
    """D-074 / AC-002 / FR-002 — D-069's fail-closed writer, defeated one frame
    up by the dispatch lambda.

    ``foundry_add_observation`` carries ``target_kind: str = ""`` so that an
    undeclared subject reaches the NON_COMMENT denylist entry; recording an
    observation IS the demotion, so that path must fail closed. The lambda then
    passed ``args.get("target_kind", "comment")``, manufacturing the very
    declaration the denylist checks. Over MCP the writer's guard was never
    reached: a genuine code-behaviour finding filed with the field absent was
    RECORDED, the fabricated "comment" was persisted into observations.json
    where no auditor can distinguish it from a real declaration, and the
    tripwire — the audit signal that exists precisely to name a demotion
    attempt — stayed SILENT on the bypass.

    Why no test caught it: the only _DISPATCH-level observation test passes
    ``target_kind: "comment"`` explicitly, and casting 3's D-069 tests drive the
    writer, where the fix is. Nothing drove the DISPATCHER with the field
    absent. This is PROVE's matched pair — same finding, same classification,
    one argument apart — driven at the layer the caller actually reaches.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=0)

    # A real code-behaviour finding whose prose ALSO trips an observation class
    # (ENUMERATION), so the only thing standing between it and the observations
    # ledger is the NON_COMMENT branch.
    finding = (
        "The _DISPATCH table registers 14 handlers but the tool roster "
        "advertises 15, so one tool dispatches to nothing."
    )

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root

        declared = foundry_server._DISPATCH["Foundry-Observation"]({
            "cycle": 0, "source": "prove", "description": finding,
            "target_kind": "code",
        })
        omitted = foundry_server._DISPATCH["Foundry-Observation"]({
            "cycle": 0, "source": "prove", "description": finding,
            # target_kind deliberately NOT passed — the bypass, verbatim.
        })
    finally:
        foundry_server._project_root = previous_root

    # Both halves of the pair reach the same verdict. The omitted half used to
    # return an observation_id.
    assert declared["denylist_class"] == "NON_COMMENT", declared
    assert omitted["denylist_class"] == "NON_COMMENT", omitted
    assert omitted["missing_field"] == "target_kind", omitted

    ledger = json.loads((fdir / "observations.json").read_text(encoding="utf-8"))
    # Nothing was demoted out of the blocking ledger — the collection is not
    # merely empty, it was never created, because no write ever got past the
    # denylist.
    assert ledger.get("observations", []) == [], ledger["observations"]
    # ...and the fabricated declaration was never persisted anywhere in the
    # ledger. This is the half that made D-074 worse than D-069: the record
    # carried target_kind "comment", a declaration the caller never made, so an
    # auditor reading observations.json could not tell it from a real one.
    assert '"target_kind": "comment"' not in json.dumps(ledger)
    # ...and the tripwire fired for BOTH attempts. It used to be silent on
    # exactly the one that got through.
    assert [t["denylist_class"] for t in ledger["tripwire"]] == [
        "NON_COMMENT", "NON_COMMENT",
    ], ledger["tripwire"]




def test_the_observation_schema_advertises_no_target_kind_default(run_env):
    """D-074's other live site. ``jsonschema.validate`` never applies schema
    defaults, so ``"default": "comment"`` was inert as validation — what it did
    was tell every reader of the tool surface that omission means "comment",
    which the dispatch lambda then made true. Driven through the real
    ``list_tools`` and the SDK's own pre-dispatch validation step, so the claim
    is about the advertised schema rather than about the source text.
    """
    import jsonschema

    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=0)

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}
    prop = tools["Foundry-Observation"].inputSchema["properties"]["target_kind"]

    assert "default" not in prop, (
        "the advertised schema still promises a target_kind default. Absence "
        "must travel to the writer AS absence — a default here documents the "
        "fabrication D-074 is about, even though jsonschema will not apply it."
    )
    # The field stays optional in `required`, because the REFUSAL is what
    # teaches the caller: a jsonschema error names the property, while the
    # handler names the missing field, the denylist class, and the repair.
    assert "target_kind" not in tools["Foundry-Observation"].inputSchema["required"]
    # ...and its description says so, so a lead reading only the tool surface
    # learns that omitting it is refused rather than defaulted.
    description = prop["description"]
    assert "REQUIRED IN PRACTICE" in description, description
    assert "Foundry-Defect" in description, description

    # fallout CT-017 / GI-027 / AC-018 / FR-044 (D-182) — AND IT CARRIES THE ONE
    # EXCEPTION THE HANDLER HAS, WHICH THIS PIN USED TO FREEZE OUT.
    # ----------------------------------------------------------------------
    # `foundry_add_observation` passes
    # `comment_subject_required=classification != TEMPER_CANDIDATE` and its
    # docstring names the exception; the published sentence said the opposite in
    # two places — "Omitting the field is refused" and "A finding about code ...
    # belongs in Foundry-Defect" — and this pin asserted both substrings, so the
    # error was held in place by the guard. Driven at HEAD: a
    # TEMPER_CANDIDATE with no target_kind was ACCEPTED with no tripwire, both
    # in-process and over the real wire.
    #
    # It is not cosmetic: this schema is what a PROVE sub-agent reads at
    # dispatch, and "a finding about code is a defect" is GI-027's own violation
    # clause ("PROVE filing a candidate as a defect") written as an instruction.
    assert "TEMPER_CANDIDATE" in description, description
    assert "EXCEPTION" in description, description
    # ...and the DERIVABILITY half, on the field that decides it: only the four
    # comment-prose classes are derived, so a caller told "derived when omitted"
    # and nothing else can never reach the candidate channel at all.
    classification = tools["Foundry-Observation"].inputSchema["properties"]["classification"]
    assert "TEMPER_CANDIDATE" in classification["description"], classification
    assert "Foundry-Observations" in classification["description"], classification
    assert vocab.TEMPER_CANDIDATE in classification["enum"], classification

    # THE PUBLISHED SURFACE AND THE HANDLER, DRIVEN TOGETHER — because two
    # sentences agreeing is not the claim; the claim is that the door behaves
    # the way the sentence says.
    from foundry_mcp.tools.foundry import foundry_add_observation

    accepted = foundry_add_observation(
        cycle=0, source="prove",
        description="probe the roster rung against a revised roster",
        classification=vocab.TEMPER_CANDIDATE,
        project_root=str(project_root),
    )
    assert accepted.get("error") is None, accepted
    assert accepted["observation_id"], accepted
    ledger = json.loads((fdir / "observations.json").read_text(encoding="utf-8"))
    assert ledger.get("tripwire", []) == [], ledger["tripwire"]

    # ...and the comment-prose lane is unchanged: omission is still refused
    # there, which is what keeps the first half of this description true.
    refused = foundry_add_observation(
        cycle=0, source="prove", description="the comment cites a line that moved",
        project_root=str(project_root),
    )
    assert refused.get("error"), refused
    assert refused["denylist_class"] == "NON_COMMENT", refused

    # The SDK validates before dispatch; an omitted target_kind must survive
    # that step, or the handler's named refusal is unreachable.
    args = {"cycle": 0, "source": "prove", "description": "handler never calls the store"}
    jsonschema.validate(instance=args, schema=tools["Foundry-Observation"].inputSchema)
    assert "target_kind" not in args, "validation must not inject a declaration"




# --------------------------------------------------------------------------- #
# D-036 — the Sync path fires the never-demote audit tripwire
# --------------------------------------------------------------------------- #


def test_sync_denylist_hit_fires_the_tripwire_end_to_end(run_env):
    """D-036 / AC-002 / FR-002, driven through Foundry-Sync to the ledger.

    ``foundry_sync_defects``'s auto-demotion branch read
    ``never_demote_class(finding) is None`` and skipped everything downstream on
    a match. The ENFORCEMENT half worked — the finding stayed a defect — while
    the AUDIT half was dead: ``record_denylist_tripwire`` (which tools/foundry.py
    exports precisely for this call site, and whose docstring names it) could
    not fire, so ``observations.json.tripwire`` stayed empty across every
    Sync-path denylist scenario. Live-proved before the fix: a
    SECURITY_PROPERTY_CLAIM comment finding through Sync left ``tripwire == []``.

    The finding below is deliberately BOTH comment-drift prose — it classifies
    as LINE_DRIFT_CITE, so it would be demoted to an observation on its own —
    and a security claim, which vocab's precedence rule says outranks that.
    Only the denylist keeps it a defect, which is what makes the tripwire the
    thing under test rather than an incidental side effect.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=4)

    finding = {
        "source": "trace",
        "type": "WRONG",
        "description": (
            "the comment cites line 88 for the csrf token check but the line "
            "number is stale and the check moved to the middleware"
        ),
        "target_kind": "comment",
        "symbol": "submit_form",
        "file": "src/api/forms.py",
        # CT-001 / CT-002: the door refuses without these, so a finding that
        # omitted them would never reach the denylist rung under test.
        "tier": FIXTURE_TIER,
        "class": FIXTURE_CLASS,
    }

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Sync"]({
            "cycle": 4,
            "findings": [finding],
        })
    finally:
        foundry_server._project_root = previous_root

    assert result.get("ok") is True, result

    # Enforcement half, unchanged: a denylist match is never demoted.
    defects = json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]
    assert len(defects) == 1, defects
    assert defects[0]["status"] == "open"
    observations = json.loads((fdir / "observations.json").read_text(encoding="utf-8"))
    assert observations.get("observations", []) == []

    # Audit half — the part that was dead.
    fired = observations["tripwire"]
    assert len(fired) == 1, fired
    assert fired[0]["denylist_class"] == vocab.SECURITY_PROPERTY_CLAIM
    # The source is attributed verbatim to the stream that filed it, and the
    # cycle is the SERVER's, not the caller's declaration.
    assert fired[0]["source"] == "trace"
    assert fired[0]["cycle"] == 4
    assert fired[0]["symbol"] == "submit_form"

    # ...and the lead is told in the response, not only in the ledger.
    assert result["denylist_tripwires"][0]["denylist_class"] == (
        vocab.SECURITY_PROPERTY_CLAIM
    )




def test_sync_refuses_a_clean_comment_finding_without_firing_a_tripwire(run_env):
    """The other side of D-036: routing the decision through
    ``record_denylist_tripwire`` must not turn ordinary comment-drift prose into
    a tripwire. Its NON_COMMENT fallback cannot fire under the declared-comment
    guard, so a legitimate comment-prose finding is refused with the audit
    channel silent.

    D-098 changed the OUTCOME and not the audit property under test: the finding
    is now refused at this door exactly as at Foundry-Defect, rather than routed
    into observations.json. What this test still pins is that no tripwire fires
    for it — the denylist had nothing to do with the decision.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=2)

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Sync"]({
            "cycle": 2,
            "findings": [{
                "tier": FIXTURE_TIER,
                "class": FIXTURE_CLASS,
                "source": "trace",
                "type": "WRONG",
                # The same LINE_DRIFT_CITE prose as the test above, minus the
                # security claim — so the ONLY difference between demotion and
                # a tripwire is the denylist, which is the thing under test.
                "description": (
                    "the comment cites line 88 but the symbol moved and the "
                    "line number is stale"
                ),
                "target_kind": "comment",
                "file": "src/api/forms.py",
            }],
        })
    finally:
        foundry_server._project_root = previous_root

    assert "comment-prose observation class" in result.get("error", ""), result
    assert result.get("added", 0) == 0, result
    assert "denylist_tripwires" not in result

    observations = (
        json.loads((fdir / "observations.json").read_text(encoding="utf-8"))
        if (fdir / "observations.json").exists() else {}
    )
    # The audit channel is SILENT — that is the D-036 property, and it survives
    # the finding being refused rather than demoted.
    assert observations.get("tripwire", []) == []




def test_an_ordinary_defect_through_sync_fires_no_tripwire(run_env):
    """The noise guard on the same change. ``record_denylist_tripwire`` reports
    NON_COMMENT for any finding whose subject is not a declared comment, so
    calling it for EVERY synced finding would fire a tripwire on every ordinary
    defect and bury the real ones. It is scoped to demotion attempts."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    _write_state(fdir, phase="F2", cycle=1)

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Sync"]({
            "cycle": 1,
            "findings": [{
                "tier": FIXTURE_TIER,
                "class": FIXTURE_CLASS,
                "source": "trace",
                "type": "UNWIRED",
                "description": "the submit handler never calls the token store",
                "file": "src/api/forms.py",
            }],
        })
    finally:
        foundry_server._project_root = previous_root

    assert result["added"] == 1, result
    assert "denylist_tripwires" not in result
    obs_path = fdir / "observations.json"
    if obs_path.exists():
        assert json.loads(obs_path.read_text(encoding="utf-8")).get("tripwire", []) == []




def test_liveness_tool_is_registered_and_dispatched():
    """CT-004 registration half: the tool is declared with the optional agent
    identifier and dispatched by name to its handler."""
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    liveness = next(t for t in tools if t.name == "Foundry-Liveness")

    # "none, or an agent identifier" — the empty-input case is binding.
    assert liveness.inputSchema.get("required", []) == []
    assert "agent" in liveness.inputSchema["properties"]
    assert "Foundry-Liveness" in foundry_server._DISPATCH

    # D-002: the handler's stall_seconds override is implemented and tested,
    # and was declared nowhere — so the SDK rejected any call carrying it and
    # the parameter was unreachable over MCP. commands/start.md tells the lead
    # to pass it, which made the gap a doc/behaviour contradiction too.
    assert "stall_seconds" in liveness.inputSchema["properties"]
    assert liveness.inputSchema["properties"]["stall_seconds"]["type"] == "number"
    # No schema bound on the value: the handler already refuses a non-positive
    # threshold BY NAME, and an `exclusiveMinimum` here would pre-empt that with
    # a raw validator message — the D-039 failure, repeated on another tool.
    assert "exclusiveMinimum" not in liveness.inputSchema["properties"]["stall_seconds"]
    assert "minimum" not in liveness.inputSchema["properties"]["stall_seconds"]




def test_liveness_stall_seconds_is_forwarded_to_the_handler(run_env):
    """D-002 driven: the override must change the ANSWER, not merely validate.

    ``_dispatch_liveness`` passed only ``agent``, so a lead following
    commands/start.md's "pass stall_seconds= to override" got the 900s default
    silently. Here one agent sits 10 minutes idle: under the default it is
    progressing, under a 60s override it is not — the same ledger, two verdicts,
    which is only possible if the value crossed the dispatcher.
    """
    import jsonschema

    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    pdir = fdir / "progress"
    pdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    stamp = (now - timedelta(minutes=10)).isoformat()
    (pdir / "casting-4.jsonl").write_text(
        json.dumps({"timestamp": stamp, "phase": "CAST", "step": "writing code"}) + "\n",
        encoding="utf-8",
    )

    tools = asyncio.run(foundry_server.list_tools())
    liveness = next(t for t in tools if t.name == "Foundry-Liveness")
    args = {"stall_seconds": 60}
    # The SDK's own pre-dispatch validation, which used to reject this call.
    jsonschema.validate(instance=args, schema=liveness.inputSchema)

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        overridden = foundry_server._DISPATCH["Foundry-Liveness"](args)
        default = foundry_server._DISPATCH["Foundry-Liveness"]({})
    finally:
        foundry_server._project_root = previous_root

    assert overridden["ok"] is True, overridden
    assert overridden["stall_threshold_seconds"] == 60
    assert default["stall_threshold_seconds"] != 60
    # Ten minutes of silence: stalled at a 60s threshold, fine at the default.
    assert "casting-4" in overridden["needs_attention"]
    assert "casting-4" not in default["needs_attention"]




def test_liveness_bad_stall_seconds_reaches_the_handlers_named_refusal(run_env):
    """The reason no schema bound was added: the handler names the offending
    value and the legal range, and that message is what an MCP caller must see
    rather than a jsonschema string."""
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Liveness"]({"stall_seconds": 0})
    finally:
        foundry_server._project_root = previous_root

    assert result["ok"] is False, result
    assert "stall_seconds" in result["error"]
    assert result["hint"]




def test_no_enum_literal_is_re_declared_in_the_server_schemas():
    """FR-013 / key_link: server.py must READ the vocabularies, never re-type
    them. The AC-013 class of defect was exactly this file's hand-typed enums
    drifting from the runtime guards."""
    from foundry_mcp import server as foundry_server

    tools = asyncio.run(foundry_server.list_tools())
    by_name = {t.name: t for t in tools}

    assert by_name["Foundry-Stream"].inputSchema["properties"]["stream"]["enum"] == sorted(
        vocab.STREAM_WIRE_IDS
    )
    defect_props = by_name["Foundry-Defect"].inputSchema["properties"]
    assert defect_props["source"]["enum"] == sorted(vocab.DEFECT_SOURCE_IDS)
    assert defect_props["defect_type"]["enum"] == sorted(vocab.DEFECT_TYPES)
    sync_props = by_name["Foundry-Sync"].inputSchema["properties"]["findings"]["items"]["properties"]
    assert sync_props["source"]["enum"] == sorted(vocab.DEFECT_SOURCE_IDS)
    assert sync_props["type"]["enum"] == sorted(vocab.DEFECT_TYPES)




def test_liveness_registration_reaches_the_handler_through_dispatch(run_env, tmp_path):
    """AC-021 through the surface this casting owns.

    The Tool declaration and the _DISPATCH entry are casting 2's; the
    ``foundry_liveness`` handler is another casting's. What is verified here is
    that the registration actually REACHES it — the lead drives the tool by
    name, with and without the optional agent identifier, and gets per-agent
    last-progress ages back with a stalled agent flagged distinctly from one
    that is progressing.

    The identifier is dispatched POSITIONALLY on purpose, so this registration
    does not depend on the handler's parameter name.
    """
    from foundry_mcp import server as foundry_server

    project_root, fdir = run_env
    pdir = fdir / "progress"
    pdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)

    def _line(minutes_ago: int, phase: str, step: str) -> str:
        ts = (now - timedelta(minutes=minutes_ago)).isoformat()
        return json.dumps({"timestamp": ts, "phase": phase, "step": step}) + "\n"

    # One agent advancing a minute ago; one silent for 40 minutes.
    (pdir / "casting-7.jsonl").write_text(
        _line(6, "CAST", "read floor") + _line(1, "CAST", "writing code"),
        encoding="utf-8",
    )
    (pdir / "casting-9.jsonl").write_text(
        _line(45, "CAST", "read floor") + _line(40, "CAST", "read floor"),
        encoding="utf-8",
    )

    previous_root = foundry_server._project_root
    try:
        foundry_server._project_root = project_root

        # CT-004's "none" input: the whole roster.
        roster = foundry_server._DISPATCH["Foundry-Liveness"]({})
        assert roster["ok"] is True, roster
        by_agent = {a["agent"]: a for a in roster["agents"]}
        assert set(by_agent) == {"casting-7", "casting-9"}

        # Per-agent last-progress AGE is what comes back...
        assert by_agent["casting-7"]["last_progress_age_seconds"] < 120
        assert by_agent["casting-9"]["last_progress_age_seconds"] > 2000

        # ...and the stalled agent is flagged distinctly from the progressing one.
        assert by_agent["casting-7"]["status"] != by_agent["casting-9"]["status"]
        assert "casting-9" in roster["needs_attention"]
        assert "casting-7" not in roster["needs_attention"]

        # CT-004's "an agent identifier" input, threaded through the lambda.
        one = foundry_server._DISPATCH["Foundry-Liveness"]({"agent": "casting-9"})
        assert [a["agent"] for a in one["agents"]] == ["casting-9"]
    finally:
        foundry_server._project_root = previous_root




def _mentions_state_json(node: ast.AST) -> bool:
    """True when the expression subtree names the state file.

    Keyed on the ``"state.json"`` literal rather than on ``_load_json`` so a
    reader that reaches the file by some other route -- ``json.loads(
    (fdir / "state.json").read_text())`` -- is caught by the same rule.
    """
    return any(
        isinstance(n, ast.Constant) and n.value == "state.json" for n in ast.walk(node)
    )




def _package_modules(root: Path) -> list[Path]:
    """Every source module in the package tree rooted at ``root``.

    Membership is derived on BOTH axes -- the files in a directory and the
    directories in the package -- so neither a new module nor a new
    subpackage has to be remembered anywhere. ``__init__.py`` is included
    for the same reason: excluding it by name would be one more typed
    exclusion, and an empty file costs nothing to parse.
    """
    return sorted(root.rglob("*.py"))




def _scan(modules: list[Path], rule) -> tuple[list[str], list[str]]:
    """Run one ``(seen, offenders)`` rule over a corpus and union both halves.

    D-142's shape, applied uniformly. ``seen`` is a list of ``module#function``
    strings naming every site the rule's recogniser identified as a MEMBER of
    the class it polices -- offending or not -- and it exists because
    ``assert not offenders`` is green in two different worlds: the one where
    the corpus is clean, and the one where the derivation has quietly stopped
    recognising the corpus's spelling. A rule whose scan reports zero members
    is not passing; it is blind, and the caller asserts against ``seen`` to
    tell the two apart by name.

    ``test_spawn_progress`` applies the identical change to the manifest-scan
    family; the tuple shape is the agreed contract between the two files.
    """
    seen: list[str] = []
    offenders: list[str] = []
    for path in modules:
        module_seen, module_offenders = rule(path)
        seen.extend(module_seen)
        offenders.extend(module_offenders)
    return sorted(set(seen)), sorted(set(offenders))




def _raw_state_cycle_reads(path: Path) -> tuple[list[str], list[str]]:
    """``(cycle readers seen, offenders)`` for one module.

    Parsed from the file on disk rather than from a list maintained beside it,
    so the guard cannot be satisfied by updating a copy and forgetting a call
    site -- and so a NEW module that starts reading the counter is covered the
    day it is written, without anyone remembering to enrol it.

    Only ``Load`` subscripts count: ``state["cycle"] = _current_cycle(fdir) + 1``
    is the boundary increment writing the counter, not a reader bypassing it.

    D-142: the GUARDED readers are recognised and reported in the first
    element, and skipped only when deciding who OFFENDS. The old shape
    ``continue``d past them before looking at anything, so the two functions
    that definitionally hold this rule's shape -- the leaf's ``current_cycle``
    and ``derive_cycle_count``, where every read of the counter now lives --
    were the two the scan could not see, and ``assert not offenders`` stayed green when
    ``_mentions_state_json`` stopped recognising the package's spelling (a
    ``"state.json"`` hoisted into a module constant empties it outright).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen: list[str] = []
    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        state_names = {
            target.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Assign) and _mentions_state_json(node.value)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        # fallout GI-024 — AND A THIRD: THE (document, problem) UNPACK.
        #
        # `foundry_state.read_document` returns a PAIR, so the leaf reader every
        # cycle read now routes through binds the document by tuple unpacking:
        # `state, _ = read_document(run_dir / "state.json")`. That target is an
        # `ast.Tuple`, not an `ast.Name`, so the comprehension above saw nothing
        # and the whole derivation went blind on the one module that matters —
        # which the anchor below caught, exactly as it was built to. The rule is
        # still the BINDING and not the syntax that produces it.
        state_names |= {
            element.id
            for node in ast.walk(fn)
            if isinstance(node, ast.Assign) and _mentions_state_json(node.value)
            for target in node.targets
            if isinstance(target, (ast.Tuple, ast.List))
            for element in target.elts
            if isinstance(element, ast.Name)
        }
        # D-098/D-103 added a SECOND way to bind the state document:
        # `with _document_transaction(state_path) as state:`. A `with` binding
        # is not an ast.Assign, so the scan above could not see it and a reader
        # could have bypassed the guard through the new route undetected. The
        # rule is the binding, not the syntax that produces it.
        state_names |= {
            item.optional_vars.id
            for node in ast.walk(fn)
            if isinstance(node, (ast.With, ast.AsyncWith))
            for item in node.items
            if isinstance(item.optional_vars, ast.Name)
            and _mentions_state_json(item.context_expr)
        }
        for node in ast.walk(fn):
            base: ast.AST | None = None
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "cycle"
            ):
                base = node.func.value
            elif (
                isinstance(node, ast.Subscript)
                and isinstance(node.ctx, ast.Load)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value == "cycle"
            ):
                base = node.value
            if base is None:
                continue
            if not (
                (isinstance(base, ast.Name) and base.id in state_names)
                or _mentions_state_json(base)
            ):
                continue
            seen.append(f"{path.name}#{fn.name}")
            if fn.name not in GUARDED_CYCLE_READERS:
                offenders.append(f"{path.name}::{fn.name}:{node.lineno}")
    return sorted(set(seen)), sorted(set(offenders))




def test_every_state_cycle_read_goes_through_a_guarded_reader():
    """D-059's root cause, asserted as a property of the whole installed package.

    Before the fix this reported exactly the four sites PROVE named:
    foundry_next_action:3225, _format_status_display:3413,
    _compute_next_action:3814, foundry_get_context:4084.

    THE BOUNDARY, and why it is the package: every module under
    ``foundry_mcp`` -- the package root and every subpackage, anchored on the
    package's own ``__init__`` rather than on a module that happens to sit one
    level down. D-066: this scan read ``Path(artifacts.__file__).parent.glob("*.py")``,
    which derived its members WITHIN tools/ but typed the directory, so the
    module MOST on the MCP request path was the one it could not see --
    server.py owns ``_DISPATCH`` and the ``list_tools``/``call_tool`` handlers,
    and it carries zero total cycle readers of its own. The old scope note
    offered three reasons for stopping at tools/ and none of them reached
    server.py: it is inside the request path, it holds no reader of its own to
    allow-list, and it is a file this casting already owns. Schemas/ and
    parsers/ hold only pure-data modules today, but the boundary is drawn at
    the package anyway: "derived over the directories" closes the
    directory-membership class exactly as "derived over the files" closed the
    file-membership class, and a scan that is right only for today's directory
    layout is the same defect waiting on the next subpackage.

    The two offline readers stay outside, now for a structural reason rather
    than a judgement call: plugins/foundry/scripts/measure-run.py
    (_read_state_cycle_count) and plugins/foundry/scripts/migrate-archive.py
    (_as_cycle) are not in this package, and not in the wheel, at all. Each
    already carries its OWN total reader with the same bool/int/negative guard,
    and neither runs inside a tool call. If either ever grows a raw read, it
    needs its own guard next to it.
    """
    pkg = Path(foundry_mcp.__file__).resolve().parent
    modules = _package_modules(pkg)
    assert modules, f"no modules discovered under {pkg}"

    # D-066's own regression assertion. Derived independently of the scan
    # (os.walk, not rglob) so a future narrowing to a directory literal --
    # `[*pkg.glob("*.py"), *(pkg / "tools").glob("*.py")]`, say -- fails here
    # by name instead of silently shrinking what the guard below can see.
    walked = {
        Path(dirpath).resolve()
        for dirpath, _dirs, files in os.walk(pkg)
        if any(f.endswith(".py") for f in files)
    }
    assert {p.parent for p in modules} == walked, (
        f"the scan covers {sorted(str(d.relative_to(pkg)) for d in {p.parent for p in modules})} "
        f"but the package holds source in "
        f"{sorted(str(d.relative_to(pkg)) for d in walked)}. Membership must be "
        f"DERIVED over directories as well as over files -- naming the "
        f"directories is D-066, the same defect one level up."
    )

    seen, offenders = _scan(modules, _raw_state_cycle_reads)
    # D-142's anchor. `assert not offenders` alone is green when the package is
    # clean AND when `_mentions_state_json` has stopped recognising the
    # package's spelling -- hoist the "state.json" literal into a module
    # constant and every binding this scan tracks vanishes with it. The two
    # TOTAL readers are where every read of the counter now lives, so they are
    # the sites this derivation must still see, by name.
    assert {
        "foundry_state.py#current_cycle",
        "foundry_state.py#derive_cycle_count",
    } <= set(seen), (
        f"the scan recognised {seen} as state-cycle readers, which does not "
        f"include the two TOTAL readers the whole package routes through. The "
        f"derivation has gone blind (the `state.json` binding or the `cycle` "
        f"index is spelled some way this scan no longer tracks), so the "
        f"offender assertion below is vacuous. Fix the recogniser -- do not "
        f"weaken this anchor."
    )
    assert not offenders, (
        f"{offenders} read state.json's 'cycle' directly instead of through a "
        f"guarded reader ({sorted(GUARDED_CYCLE_READERS)}). A raw read hands on "
        f"whatever the state file holds: a str/None/list/dict crashes the very "
        f"next ordered comparison, and -3 or 2.5 propagates silently into "
        f"responses and into written verdict records. Route the read through "
        f"_current_cycle -- do not shrink this assertion or add to the "
        f"allow-list, which exists for TOTAL readers only."
    )




def test_guard_catches_raw_reads_outside_the_tools_subpackage(tmp_path):
    """D-066 adjacent-path test (AC-013).

    The path the defect was found on is "a raw read in a module under
    tools/" -- the only path the old directory literal could reach. The
    ADJACENT path this drives is the two positions that literal excluded: a
    module at the PACKAGE ROOT (server.py's position, which owns _DISPATCH)
    and a module in a NON-tools subpackage. Both carry the exact shape the
    detector exists to catch -- a raw read that never touches _load_json --
    and both must be discovered and named.

    Hermetic on purpose: it runs the guard's own two helpers over a synthetic
    package under tmp_path, so it proves the mechanism without mutating the
    real tree the way the defect's driving evidence had to.
    """
    raw_reader = (
        "import json\n"
        "from pathlib import Path\n"
        "\n"
        "\n"
        "def _sneaky_cycle_reader(fdir):\n"
        "    return json.loads((Path(fdir) / 'state.json').read_text())['cycle']\n"
    )
    pkg = tmp_path / "fake_pkg"
    (pkg / "sub").mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "at_package_root.py").write_text(raw_reader, encoding="utf-8")
    (pkg / "sub" / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "sub" / "in_a_subpackage.py").write_text(raw_reader, encoding="utf-8")

    # Both axes of membership: the root module and the nested one are found,
    # and __init__.py is not excluded by name.
    modules = _package_modules(pkg)
    assert sorted(p.relative_to(pkg).as_posix() for p in modules) == [
        "__init__.py",
        "at_package_root.py",
        "sub/__init__.py",
        "sub/in_a_subpackage.py",
    ]

    # ...and the detector names both, at the line the read is on — and reports
    # both as members it SAW, which is what the package-wide anchor rests on.
    seen, offenders = _scan(modules, _raw_state_cycle_reads)
    assert offenders == [
        "at_package_root.py::_sneaky_cycle_reader:6",
        "in_a_subpackage.py::_sneaky_cycle_reader:6",
    ]
    assert seen == [
        "at_package_root.py#_sneaky_cycle_reader",
        "in_a_subpackage.py#_sneaky_cycle_reader",
    ]




def _dispatched_orchestrator_handlers() -> list[str]:
    """Orchestrator functions reachable over MCP, read from server.py's source.

    ``_DISPATCH``'s values are lambdas, so the target is not introspectable at
    runtime — the names are collected from the AST of the ``_DISPATCH``
    assignment instead. Derived, not a list kept beside the code: an
    orchestrator tool added tomorrow is enrolled the day it is dispatched.
    """
    import foundry_mcp.server as srv

    tree = ast.parse(Path(srv.__file__).read_text(encoding="utf-8"))
    dispatch = next(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name) and t.id == "_DISPATCH"
    )
    referenced = {n.id for n in ast.walk(dispatch) if isinstance(n, ast.Name)}
    # fallout FR-034 — CALLABLES ONLY, AND THAT IS PART OF THE DERIVATION.
    #
    # A dispatch entry names more than its handler: `args.get("caller",
    # LEAD_CALLER)` puts a vocabulary CONSTANT inside the map, and a constant
    # this package also carries would otherwise be enrolled as an entry point
    # and looked up in a table of function bodies it can never be in. The
    # question this derivation asks is "which of this package's FUNCTIONS does
    # the wire reach", so the callable test is the question, not a filter
    # softening it.
    return sorted(
        name for name in referenced
        if orchestration_has(name)
        and callable(getattr(owning_module(name), name, None))
    )




def test_every_orchestrator_entry_point_runs_the_artifact_guard():
    """Derived membership over the DISPATCH map, not a list beside it.

    The handler set is read from server.py's ``_DISPATCH`` and filtered to the
    ones this module defines, so an orchestrator tool added tomorrow is
    enrolled the day it is dispatched rather than the day someone remembers.

    D-157 — "A GUARD IS CALLED" AND "THE GUARD HAS THIS MEMBER" ARE TWO CLAIMS.
    This test asserted only the first, so a guard whose membership had narrowed
    back to the run directory would satisfy it at every door while every door
    went on acting on an unreadable declared EXTERNAL input. The membership is
    therefore asserted here too, structurally: the guard each door runs must be
    the one that reaches `_declared_external_inputs`. The behavioural half --
    each door driven on a corrupt external input and required to refuse BY NAME,
    all the way through the rendering -- is
    `test_every_orchestrator_door_refuses_a_corrupt_declared_external_input`.
    """
    entry_points = _dispatched_orchestrator_handlers()
    assert len(entry_points) >= 12, entry_points

    # fallout FR-004 / AC-014 — THE PIN FOLLOWS THE DEFINITION, NOT THE FILE.
    #
    # Commit group (0) moved `_artifact_guard`, `_run_artifact_problems` and
    # `_declared_external_inputs` into `tools/artifacts.py` and left the DOORS
    # here. Both modules are parsed and their bodies merged, so the entry-point
    # half is still read off the doors' own source while the guard chain is read
    # off the leaf that now holds it. Merging rather than switching is what
    # keeps this test honest through the carve: it names symbols, and a symbol
    # is found wherever the package defines it.
    bodies = {}
    for module in (*ORCHESTRATION, artifacts):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                bodies.setdefault(node.name, node)

    def calls(name: str, callee: str) -> bool:
        return any(
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == callee
            for n in ast.walk(bodies[name])
        )

    missing = [name for name in entry_points if not calls(name, "_artifact_guard")]
    assert missing == [], (
        f"orchestrator MCP entry points that never run _artifact_guard: {missing}"
    )

    # ...and the guard they run is the one that ASKS ABOUT THE EXTERNAL INPUTS.
    # Traced through the source rather than assumed, so severing the link at
    # either rung -- the guard dropping the problems call, or the problems scan
    # dropping the external-input scan -- fails here by name at every door at
    # once, which is the level the class lives at.
    assert calls("_artifact_guard", "_run_artifact_problems"), (
        "_artifact_guard no longer runs _run_artifact_problems, so what every "
        "door above calls is not the guard this rule is about."
    )
    assert calls("_run_artifact_problems", "_declared_external_inputs"), (
        "_run_artifact_problems no longer reaches _declared_external_inputs, so "
        "the guard's membership has narrowed back to the run directory and the "
        "run's DECLARED EXTERNAL INPUTS -- the spec at state.json's spec_path "
        "among them -- are outside every door's guard again (D-145, D-157)."
    )




# --------------------------------------------------------------------------- #
# D-196 — A SUPERSEDED HELPER WAS LEFT BEHIND BY ITS OWN FIX.
#
# `_spec_relative_path` (singular) answered "where is this run's spec".
# `_spec_relative_paths` (plural) was added beside it to fix D-102, the FULL
# rule was rewired to call the plural, and the singular was left in the file —
# reachable by nothing, with a docstring presenting it as a live sibling ("ONE
# spelling, which is why `_spec_relative_paths` exists beside it"). So the next
# reader had to derive from CALL-SITE ABSENCE that it was dead, which is the
# one thing a docstring should never make them do. FR-032 asks for one
# constant rather than several spellings; two resolvers, one unreachable, is
# that duplication surviving its own fix.
#
# The pin is over the MODULE, not over that one name: a test naming
# `_spec_relative_path` would pass the day it is deleted and catch nothing
# afterwards, which is how the class comes back on the next superseded helper.
#
# D-199 — AND THE PIN ITSELF WAS SCOPED TO ONE MODULE WHILE THE CLASS WAS NOT.
#
# As shipped it read every plugin .py file for CALLERS and took its SUBJECTS
# from `foundry_orchestrator.py` alone: wide on the reference side, one module
# deep on the subject side. So the class had live instances the pin could not
# see — `scripts/measure-run.py#_parse_iso8601`, whose last call site commit
# c1727f3 deleted; `tests/test_orchestrator_gates.py#_ledger_transaction_
# callers`, dead directly beneath a comment reading "membership below is
# computed from the call graph, not from this"; and
# `tests/test_spawn_progress.py#_is_text_read`. A pin whose subject set is
# narrower than the class it names is the same shape as the helper it catches.
#
# The subject set is now every .py file the plugin's own source holds — src,
# scripts and tests alike — with the reference sweep, the derivation guards and
# the installed-dependency exclusion unchanged.
#
# D-202 — AND THEN ONE AXIS OF THE SUBJECT SET WAS WIDENED AND THE OTHER LEFT.
#
# D-199 widened the FILE axis to the whole plugin and left the NODE-TYPE axis at
# `ast.FunctionDef` / `ast.AsyncFunctionDef`, so a superseded BINDING was never
# a subject and the class kept live instances the pin was structurally unable to
# see. Two of them at f5b487b: `tests/test_report.py#_DOOR_WRITTEN_DEFECT_FIELDS`,
# a nine-field frozenset superseded by an inline tuple in the same module and
# named nowhere in code — its only surviving mention was inside `_scenario_rows`'
# docstring, which went on narrating it as live, the exact "derive it from
# CALL-SITE ABSENCE" harm D-196 was filed on — and
# `tests/test_observations.py#_PIN_SENTINEL`, a bare alias with no reader.
# Seven more sat in `display.py`'s ANSI palette.
#
# A dead frozenset is the same defect as a dead function: a name whose reader
# was rewired away and which nobody deleted, presented to the next reader as
# live. So the subject set is now every PRIVATE MODULE-LEVEL BINDING the plugin
# ships — `ast.Assign` and `ast.AnnAssign` targets and private `ast.ClassDef`s
# alongside the two function types — over the same file set D-199 established.
# Both axes at once, because widening either alone is what produced this defect
# twice: D-197 widened a suffix table and left the question keyed on the suffix,
# D-199 widened a file set and left the node types, and both were re-filed the
# following cycle.
#
# A BINDING'S OWN STATEMENT DOES NOT VOUCH FOR IT, for the same reason a
# recursive call does not vouch for a function: the reference sweep counts
# `ast.Name` loads, and an assignment's own TARGET is an `ast.Name`. So a
# subject statement contributes its children's names minus its own targets —
# which is what makes `_PIN_SENTINEL = PIN_SENTINEL` report the public name as
# referenced and the private one as an orphan, rather than the alias vouching
# for itself.
# --------------------------------------------------------------------------- #

#: Private module-level functions some MECHANISM reaches without naming them.
#:
#: Not "unreferenced and we are fine with it" — a private helper with no caller
#: is dead code by definition. The escape hatch is only for a name a mechanism
#: reaches WITHOUT naming it in source (a getattr dispatch, a plugin-style
#: registry, a collector that finds functions by decorator), and adding one
#: costs the comment beside it saying which mechanism and how. "It will be used
#: soon" is not that comment.
#:
#: THE PIN SKIPS THIS STATEMENT WHEN IT SWEEPS FOR REFERENCES, and it has to.
#: `_named_in_code` counts a string constant that IS a helper's name, because
#: `patch_everywhere(monkeypatch, "_helper", ...)` and getattr dispatch both spell a
#: call that way — so an entry here would VOUCH FOR ITSELF and the allowlist
#: would be a no-op that reads as load-bearing. Same failure as the text sweep
#: `_named_in_code`'s docstring describes, one rung further in: the mechanism
#: that is supposed to record an exception cannot also be the thing that grants
#: it.
_MECHANISM_REACHED_HELPERS: dict[str, str] = {
    # pytest collects an autouse fixture from the decorator, never from a call
    # site, so no module names it and every module in which it is declared
    # depends on it running. Five test modules declare one.
    "_isolate_active_run": "@pytest.fixture(autouse=True) — pytest collects it",
}




def _named_in_code(tree: ast.AST) -> set[str]:
    """Every identifier `tree` NAMES IN CODE — not in prose.

    THE PROSE HAS TO BE EXCLUDED OR THE PIN CANNOT FAIL. Written first as a
    text sweep, this passed at the pre-fix commit: the very comment above,
    which spells `_spec_relative_path` to explain why the pin exists, was
    counted as a reference and resurrected the dead helper. Reachability is a
    property of CODE, so it is read from the syntax tree: a load, an attribute,
    or a string that IS the name (`patch_everywhere(monkeypatch, "_helper", ...)` and
    `getattr` dispatch both spell it that way, and neither is a call site a
    grep for `_helper(` would find either).
    """
    named: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            named.add(node.id)
        elif isinstance(node, ast.Attribute):
            named.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            named.add(node.value)  # exact-match only; a docstring MENTION is not one
    return named




def _assigned_names(node: ast.AST) -> list[str]:
    """Every bare name a module-level assignment BINDS, tuple targets included.

    `_a, _b = f()` binds two, and reading only `ast.Name` targets would make
    both invisible to the pin — the same one-shape-only narrowing D-202 was
    filed on, one rung down.
    """
    if isinstance(node, ast.Assign):
        stack: list[ast.expr] = list(node.targets)
    elif isinstance(node, ast.AnnAssign):
        stack = [node.target]
    else:
        return []
    found: list[str] = []
    while stack:
        target = stack.pop()
        if isinstance(target, ast.Name):
            found.append(target.id)
        elif isinstance(target, (ast.Tuple, ast.List)):
            stack.extend(target.elts)
    return found




def _private_names_bound(node: ast.AST) -> list[str]:
    """The PRIVATE module-level names ``node`` declares — the pin's subjects.

    D-202: functions, async functions, classes and bindings alike. A dunder is
    not a private helper (`__all__`, `__version__` are the module's published
    surface), so the `__` prefix is excluded exactly as it was for functions.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        candidates = [node.name]
    else:
        candidates = _assigned_names(node)
    return [
        name for name in candidates
        if name.startswith("_") and not name.startswith("__")
    ]




def _is_the_allowlist_statement(node: ast.AST) -> bool:
    """Is ``node`` one of this file's allowlist assignments?

    Excluded from the reference sweep so an allowlist grants exceptions rather
    than manufacturing them — see the comment above each constant.

    D-217 added the second name. `_PROSE_CITES_WITH_NO_DEFINITION`'s keys are
    string constants that ARE private names, and `_named_in_code` counts those,
    so without this the day one of those names is resurrected its own excuse
    would vouch for it here — the identical hazard `_MECHANISM_REACHED_HELPERS`
    records, arriving from the other pin.
    """
    assigned = _assigned_names(node)
    return (
        "_MECHANISM_REACHED_HELPERS" in assigned
        or "_PROSE_CITES_WITH_NO_DEFINITION" in assigned
    )




def test_every_private_function_the_plugin_ships_is_reachable():
    """FR-032 verbatim: the verifier-path decision is 'derived from one constant
    rather than typed in several places'. NFR-001. D-196, D-199, D-202.

    Every private module-level NAME in every Python file the plugin ships —
    `src/`, `scripts/` and `tests/` alike, and functions, async functions,
    classes and bindings alike — must be named in code somewhere OUTSIDE its
    own defining statement, in any of those files, so a helper reached only
    from a test still counts. A name nothing names was superseded and nobody
    deleted it.

    THE SUBJECT SET IS THE WHOLE OF WHAT THE PLUGIN SHIPS, ON BOTH AXES.
    D-199 widened the FILE axis (three instances sat outside a pin scoped to
    `foundry_orchestrator.py` — one in a shipped script, two in the test
    corpus) and left the NODE-TYPE axis at functions, so D-202 found two more
    in BINDINGS: a nine-field frozenset superseded by an inline tuple whose
    only surviving mention was a docstring still narrating it as live, and a
    bare alias with no reader. Widening one axis of a two-axis predicate is
    what re-filed this class in three consecutive cycles; both are widened
    here, and the derivation guards below fail loudly if either silently
    collapses.

    THE NAME SAYS `function` AND THE SUBJECTS ARE WIDER, DELIBERATELY. D-199's
    closure persisted this locator in `defects.json`; renaming it would leave
    that record pointing at a test that no longer exists, which costs more than
    the two words of drift. The docstring is where the scope is stated.

    NAME-KEYED, WHICH CAN ONLY MAKE IT MORE PERMISSIVE. Two modules defining a
    private name of the same spelling vouch for each other. That is the same
    looseness the single-module pin had for its own recursion, it never reports
    a live name as dead, and closing it would mean resolving imports rather
    than reading names.
    """
    plugin_root = Path(artifacts.__file__).resolve().parents[4]  # .../plugins/foundry
    assert plugin_root.name == "foundry", plugin_root

    trees: dict[Path, ast.AST] = {}
    for path in sorted(plugin_root.rglob("*.py")):
        # THE PLUGIN'S OWN SOURCE, not everything a virtualenv dropped under it.
        # A third-party module that happens to define a private helper of the
        # same name would otherwise vouch for a dead one, and the set of files
        # present would depend on whether anyone had run `uv` in this tree.
        if not _INSTALLED_DEPENDENCY_DIRS.isdisjoint(path.parts):
            continue
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
    # Derivations that silently found nothing would pass forever.
    assert len(trees) >= 50, len(trees)

    helpers: dict[str, list[str]] = {}
    functions = 0
    bindings = 0
    for path, tree in trees.items():
        for node in tree.body:
            for name in _private_names_bound(node):
                helpers.setdefault(name, []).append(
                    f"{path.relative_to(plugin_root)}:{node.lineno}"
                )
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions += len(_private_names_bound(node))
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                bindings += len(_private_names_bound(node))
    # Derivations that silently found nothing would pass forever — and after
    # D-202 that has to be asserted PER AXIS. A single `>= 100` over the union
    # stays green while the binding half collapses to zero, which is exactly
    # the failure this widening exists to make impossible.
    assert functions >= 100, functions
    assert bindings >= 100, bindings

    reachable: set[str] = set()
    for tree in trees.values():
        # A subject's OWN statement does not vouch for it: a recursive call is
        # not a caller, and an assignment's own TARGET is an `ast.Name` load to
        # the sweep. Everything else in the file it is declared in does vouch —
        # except the allowlist above, whose keys would otherwise grant
        # themselves the exception they are supposed to record.
        for node in tree.body:
            if _is_the_allowlist_statement(node):
                continue
            own = _private_names_bound(node)
            if own:
                reachable |= {
                    name
                    for child in ast.iter_child_nodes(node)
                    for name in _named_in_code(child)
                } - set(own)
            else:
                reachable |= _named_in_code(node)

    orphans = sorted(
        f"{name} ({', '.join(helpers[name])})"
        for name in helpers
        if name not in reachable and name not in _MECHANISM_REACHED_HELPERS
    )
    assert orphans == [], (
        f"private module-level name(s) reachable by nothing: {orphans}. "
        f"A function, class or binding nothing in the plugin names outside its "
        f"own defining statement was superseded and left behind (D-196, D-199, "
        f"D-202). Delete it, or make it the "
        f"survivor's helper and call it -- and if some mechanism reaches it "
        f"without naming it, record that mechanism in "
        f"_MECHANISM_REACHED_HELPERS."
    )




# --------------------------------------------------------------------------- #
# D-203 — AND THE SUBJECT SET STILL HAD A THIRD SHAPE IT COULD NOT SEE.
#
# `superseded-helper-left-unreachable` escalated across cycles 15-18 —
# `_spec_relative_path`, `_parse_iso8601`, `_DOOR_WRITTEN_DEFECT_FIELDS`, and
# then this. D-202 deleted `_PIN_SENTINEL = PIN_SENTINEL` in
# tests/test_observations.py, which was that alias's only reader, and left the
# `PIN_SENTINEL` name in the `from tests.test_spec_id_convention import (...)`
# statement above it. The reachability pin cannot reach it on EITHER of its two
# axes: its subjects are `Assign`/`AnnAssign`/`FunctionDef`/`ClassDef`, and an
# import alias is none of those, and `_private_names_bound` keeps only
# underscore-prefixed names, while `PIN_SENTINEL` is public. So the fix for one
# instance of the class created the next one, in the same statement, and
# nothing in the suite could say so.
#
# THE ROOT CAUSE IS NOT THAT NAME. It is that no pin covered a BINDING MADE BY
# AN IMPORT, which is the third way this plugin binds a module-level name and
# the one way a name can be bound without a defining statement to hang a
# subject off. So the pin below has its own two axes, and both are stated
# because widening one and leaving the other is precisely how this class was
# re-filed in four consecutive cycles:
#
#   NODE TYPES — every `ast.Import` and `ast.ImportFrom` alias, at EVERY scope.
#     A function-local import is a binding with the same failure mode; the
#     reference rule below is per-file and therefore strictly more permissive
#     about them, never less.
#   NAME VISIBILITY — PUBLIC NAMES INCLUDED. The reachability pin is private-only
#     because a public module-level function is part of a module's surface and
#     may legitimately have no in-plugin caller. An IMPORT has no such defence:
#     it is a name this module pulled in to USE, so its visibility says nothing
#     about who may read it. `PIN_SENTINEL` is public, and that alone is what
#     hid it.
#
# WHY THE REFERENCE RULE IS PER FILE, WHERE THE REACHABILITY PIN'S IS
# CORPUS-WIDE. An import binds a name in ONE module's namespace, and the name it
# binds is by construction defined and used in the module it came from — so a
# corpus-wide sweep would vouch for every unused import in the plugin,
# `PIN_SENTINEL` first among them, and the pin would be a no-op that reads as
# load-bearing. The AST rule itself is unchanged: `_named_in_code`, code and not
# prose, for the reason its docstring gives.
#
# THE TWO VOUCHERS, both mechanical:
#   `from __future__ import annotations` binds `annotations`, which nothing ever
#     reads. It is a compiler directive, not a name.
#   A RE-EXPORT is a real use: when another shipped module writes
#     `from <this module> import <name>`, this module's binding is what that
#     import resolves. `tests/conftest.py` reads `_PRUNE_DONE_FOR` off
#     `tools/evidence.py` exactly that way.
# Anything else needs an entry in `_ATTRIBUTE_READ_REEXPORTS` with its reason,
# and a stale entry FAILS rather than being tolerated — an allowlist that
# survives the thing it excuses is the boundary-moving shape this class
# escalated on.
# --------------------------------------------------------------------------- #

#: Imports a shipped module holds for a reader that reaches them by ATTRIBUTE,
#: which no `from <module> import <name>` statement records.
#:
#: Keyed by `(path suffix, bound name)`; the suffix matches at a `/` boundary so
#: one entry covers a name imported in both arms of a try/except import guard.
#: Not "unused and we are fine with it" — every entry must name the reader, and
#: `test_no_shipped_module_holds_an_unused_import` FAILS on an entry whose
#: import is gone, so an owner deleting theirs takes the entry with it.
#:
#: THE PIN SKIPS THIS STATEMENT WHEN IT SWEEPS THIS FILE FOR REFERENCES, for the
#: reason `_MECHANISM_REACHED_HELPERS` states one pin up: `_named_in_code`
#: counts a string constant that IS a name, so an entry naming an import of THIS
#: module would vouch for it and the allowlist would grant itself the exception
#: it is supposed to record.
_ATTRIBUTE_READ_REEXPORTS: dict[tuple[str, str], str] = {
    ("scripts/measure-run.py", "THUNDER_VIPER_BASELINE"): (
        "tests/test_measure_run.py::"
        "test_the_baseline_and_target_are_read_from_vocab_not_re_typed asserts "
        "`module.THUNDER_VIPER_BASELINE is vocab.THUNDER_VIPER_BASELINE` on the "
        "script loaded by path, so the import IS the surface that assertion "
        "reads — the script re-exports vocab's object rather than re-typing 22 "
        "and 8, which is the drift FR-013 built vocab.py to end."
    ),
}




def _is_the_unused_import_allowlist_statement(node: ast.AST) -> bool:
    """Is ``node`` the ``_ATTRIBUTE_READ_REEXPORTS`` assignment itself?"""
    return "_ATTRIBUTE_READ_REEXPORTS" in _assigned_names(node)




def _import_aliases(tree: ast.AST) -> list[tuple[ast.AST, str, str]]:
    """Every name an `Import`/`ImportFrom` in ``tree`` BINDS, at every scope.

    Returned as `(node, bound name, source spelling)`. `import a.b.c` binds `a`,
    not `c` — the dotted form binds the top package — and `from x import *`
    binds nothing this pin can name, so it is skipped rather than guessed at.
    `from __future__ import ...` is a compiler directive: the name it binds is
    never read by anything, here or in any Python program.
    """
    found: list[tuple[ast.AST, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            source = node.module or "."
        elif isinstance(node, ast.Import):
            source = ""
        else:
            continue
        for alias in node.names:
            if alias.name == "*":
                continue
            bound = alias.asname or alias.name.split(".")[0]
            found.append((node, bound, source or alias.name))
    return found




def _module_spellings(relative: Path) -> set[str]:
    """Every dotted name another module could import ``relative`` by.

    Each `.`-joined suffix of the path, `__init__.py` collapsing to its package.
    A segment that is not an identifier (`mcp-server`) ends the walk, because no
    import statement can spell it. Suffixes rather than one canonical name
    because the plugin is imported under several roots — `foundry_mcp.tools.x`
    from `src/`, `tests.test_x` from the test root — and a single canonical
    spelling would silently stop vouching under the other.
    """
    parts = list(relative.parts)
    parts[-1] = relative.stem
    if parts[-1] == "__init__":
        parts.pop()
    spellings: set[str] = set()
    for start in range(len(parts) - 1, -1, -1):
        if not parts[start].isidentifier():
            break
        spellings.add(".".join(parts[start:]))
    return spellings




def test_no_shipped_module_holds_an_unused_import():
    """NFR-001; FR-032's "one constant rather than typed in several places".
    D-196, D-199, D-202, D-203.

    Every name an import binds, in every Python file the plugin ships, must be
    NAMED IN CODE by the module that binds it — or be re-exported, meaning some
    other shipped module imports that name FROM this one. A name nothing in its
    own module reads was superseded and nobody deleted the import.

    THE SIBLING PIN ABOVE CANNOT SEE THIS, ON BOTH OF ITS AXES. Its subjects are
    defining statements, and an import has none; its subjects are private, and
    an imported name is as often public. D-202's own fix left `PIN_SENTINEL`
    behind in the statement it edited, which is how a class that had been
    escalated for three cycles produced its fourth instance out of its own
    remedy.

    PER-FILE, NOT CORPUS-WIDE, AND THAT IS THE ONE PLACE THIS DIFFERS FROM THE
    PIN ABOVE. An imported name is defined and used in the module it came from,
    so a corpus-wide reference sweep vouches for every unused import there is.
    The `_named_in_code` rule is unchanged; only the scope it is applied over
    is, and the docstring above explains why the two pins must differ there.
    """
    plugin_root = Path(artifacts.__file__).resolve().parents[4]  # .../plugins/foundry
    assert plugin_root.name == "foundry", plugin_root

    trees: dict[Path, ast.AST] = {}
    for path in sorted(plugin_root.rglob("*.py")):
        if not _INSTALLED_DEPENDENCY_DIRS.isdisjoint(path.parts):
            continue
        try:
            trees[path] = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
    assert len(trees) >= 50, len(trees)

    # A name some OTHER shipped module imports from this one is in use here:
    # that import statement resolves through this module's binding.
    reexported: dict[str, set[str]] = {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                # fallout AC-015 / OT-015 (D-081's class, seventh sweep) —
                # SPELLING TWO BINDS A MODULE, AND A MODULE RE-EXPORTS NOTHING.
                #
                # `from foundry_mcp.tools import evidence` puts `evidence` in
                # `node.names` and `foundry_mcp.tools` in `node.module`, so read
                # as a symbol it files `evidence` in the re-export bucket of
                # `tools` — and then vouches for an unused `evidence` binding in
                # `tools/__init__.py`, which nothing imported from anywhere.
                # Resolved on disk through `_submodules_named_by`, the reading
                # both layering walks share, so this walk knows the three
                # spellings apart instead of seeing one and guessing.
                #
                # `ast.Import` needs no arm for the same reason: it binds only a
                # module, so it can vouch for no name in one.
                modules = (
                    set()
                    if node.level
                    else _submodules_named_by(
                        node.module, [a.name for a in node.names]
                    )
                )
                symbols = {a.name for a in node.names} - modules
                reexported.setdefault(node.module.rsplit(".", 1)[-1], set()).update(
                    symbols
                )
                reexported.setdefault(node.module, set()).update(symbols)

    orphans: list[str] = []
    allowed_hits: set[tuple[str, str]] = set()
    plain_imports = from_imports = nested = 0
    for path, tree in trees.items():
        relative = path.relative_to(plugin_root)
        posix = relative.as_posix()
        referenced: set[str] = set()
        for node in tree.body:
            if _is_the_unused_import_allowlist_statement(node):
                continue
            referenced |= _named_in_code(node)
        spellings = _module_spellings(relative)
        vouched = set().union(*(reexported.get(s, set()) for s in spellings)) \
            if spellings else set()
        scoped = {
            id(inner)
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
            for inner in ast.walk(node)
            if isinstance(inner, (ast.Import, ast.ImportFrom))
        }

        for node, bound, source in _import_aliases(tree):
            if isinstance(node, ast.Import):
                plain_imports += 1
            else:
                from_imports += 1
            if id(node) in scoped:
                nested += 1
            if bound in referenced or bound in vouched:
                continue
            allow = next(
                (
                    key for key in _ATTRIBUTE_READ_REEXPORTS
                    if bound == key[1]
                    and (posix == key[0] or posix.endswith(f"/{key[0]}"))
                ),
                None,
            )
            if allow is not None:
                allowed_hits.add(allow)
                continue
            orphans.append(f"{posix}:{node.lineno} imports {bound} from {source}")

    # Derivations that silently found nothing would pass forever, and after
    # D-202's lesson that has to be asserted PER AXIS: a single total stays
    # green while one node type or one scope collapses to zero, which is the
    # exact failure widening both axes at once exists to make impossible.
    assert plain_imports >= 200, plain_imports
    assert from_imports >= 700, from_imports
    assert nested >= 200, nested

    assert sorted(orphans) == [], (
        f"unused import(s) in shipped modules: {sorted(orphans)}. A name an "
        f"import binds that its own module never reads was superseded and "
        f"nobody deleted the import (D-203). Delete the name from the import "
        f"statement -- and if another module reaches it by ATTRIBUTE rather "
        f"than by importing it from here, record that reader in "
        f"_ATTRIBUTE_READ_REEXPORTS."
    )

    stale = sorted(set(_ATTRIBUTE_READ_REEXPORTS) - allowed_hits)
    assert stale == [], (
        f"_ATTRIBUTE_READ_REEXPORTS entr(y/ies) excusing nothing: {stale}. The "
        f"import each one names is gone or is now read normally, so the entry "
        f"outlived the exception it records. Delete it. Tolerating a stale "
        f"entry is how an allowlist becomes the place dead names go to live."
    )




def _private_names_defined_anywhere(plugin_root: Path) -> set[str]:
    """Every private name BOUND by any Python the plugin ships.

    Permissive on purpose, and name-keyed like the reachability pin above: defs,
    classes, assignments, arguments and module-level imports all count, at any
    nesting depth, in any file. Also the DEFINITIONS inside a string constant
    that parses as Python, because this module PLANTS synthetic modules that way
    and a comment about a name bound in one is a comment about a real binding.

    Permissive can only make the pin miss a stale cite; it can never accuse a
    live one. Resolving imports per file instead would make a comment about a
    sibling module's helper a failure, and cross-module comments are most of
    what the house style writes — `escalation.py` aliases `vocab.escalation_
    status` deliberately, says so out loud, and `tests/test_escalation.py` pins
    the alias by name, so an alias is a legitimate subject of prose in a module
    that is not the one making it.

    fallout D-062 (casting 10's concern C-049) — WITH ONE TOLERANCE REMOVED: AN
    IMPORT INSIDE A STRING CONSTANT IS NOT A BINDING.

    Driven: `_overlay_unreported` had no definition anywhere in the plugin and
    resolved here anyway, because `tests/test_report.py` asserts
    ``"import _overlay_unreported" not in source`` — a string literal that
    parses as an Import node, so the harvest took the name from the very
    assertion proving the symbol is gone. That is not permissiveness missing a
    stale cite; it is the guard being fed its answer by the test that removed
    the thing. A planted module's `def` and its assignments are still harvested,
    because those really are bindings the plant creates; only the import
    statements inside one stop counting, and the depth check is the whole of the
    difference.
    """
    names: set[str] = set()

    def harvest(tree: ast.AST, depth: int) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                names.add(node.id)
            elif isinstance(node, ast.arg):
                names.add(node.arg)
            elif isinstance(node, (ast.Import, ast.ImportFrom)) and depth == 0:
                for alias in node.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif (
                depth == 0
                and isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and len(node.value) > 20
            ):
                try:
                    harvest(ast.parse(node.value), depth + 1)
                except (SyntaxError, ValueError, RecursionError):
                    pass

    for path in shipped_python_files():
        assert plugin_root in path.parents, (plugin_root, path)
        try:
            harvest(ast.parse(path.read_text(encoding="utf-8")), 0)
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
    return {n for n in names if n.startswith("_") and not n.startswith("__")}




def _private_names_cited_in_prose(path: Path) -> list[tuple[int, str]]:
    """`(line, name)` for every backtick-quoted private name in `path`'s prose.

    Prose is COMMENTS and DOCSTRINGS, which is exactly the surface
    `_named_in_code` refuses to read — the two halves are complements, and
    between them every mention of a private name in this casting's files is
    judged by one pin or the other.

    A dotted cite (`fo._helper`) is judged on its head, and a dunder is the
    module's published surface rather than a private helper, so both are
    handled as the reachability pin handles them.
    """
    source = path.read_text(encoding="utf-8")
    found: list[tuple[int, str]] = []
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type == tokenize.COMMENT:
            for match in _BACKTICK_CITE.finditer(token.string):
                found.append((token.start[0], match.group(1)))
    for node in ast.walk(ast.parse(source)):
        if not isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            continue
        doc = ast.get_docstring(node, clean=False)
        if not doc:
            continue
        first = node.body[0].lineno
        for offset, line in enumerate(doc.splitlines()):
            for match in _BACKTICK_CITE.finditer(line):
                found.append((first + offset, match.group(1)))
    return [
        (lineno, cite.split(".")[0])
        for lineno, cite in found
        if cite.split(".")[0].startswith("_")
        and not cite.split(".")[0].startswith("__")
    ]




def test_every_private_name_the_plugins_prose_cites_has_a_definition():
    """D-217. The mirror of the two pins above, over the prose they exclude.

    Every backtick-quoted private name in a comment or docstring of ANY Python
    file the plugin ships resolves to a definition somewhere in that same
    Python — or is recorded in `_PROSE_CITES_WITH_NO_DEFINITION` as a name the
    tree deliberately removed, with the removal named.

    A cite that resolves to nothing is a comment asserting a fact about code
    that is not there.

    fallout D-062 / AC-014 — THE WINDOW IS THE PACKAGE, AND IT USED TO BE EIGHT
    FILES.

    This iterated one casting's key files while stating a rule about "the Python
    the plugin ships", and the gap was not theoretical: the same helpers driven
    over the 43 shipped modules found ELEVEN unresolved cites, three of them
    PRESENT-TENSE assertions about symbols with zero definitions anywhere. All
    three sat outside the eight-file window, which is exactly why the suite was
    green over them. D-036 is the same shape one guard over — a scan window
    narrower than the rule it states — and the answer is the same: measure what
    the sentence claims.

    Both sides now read `shipped_python_files()`, so the question and the answer
    are asked over one tree. The floors are what keep a derivation that silently
    found nothing from passing forever, and they are stated against that tree
    rather than against a subset of it.
    """
    plugin_root = Path(artifacts.__file__).resolve().parents[4]  # .../plugins/foundry
    assert plugin_root.name == "foundry", plugin_root

    defined = _private_names_defined_anywhere(plugin_root)
    # A derivation that silently found nothing would pass forever, so all three
    # halves carry a floor: the universe cites resolve AGAINST, the files
    # scanned, and the cites themselves.
    assert len(defined) >= 1200, len(defined)

    files = shipped_python_files()
    assert len(files) >= 90, len(files)

    cited: dict[str, list[str]] = {}
    for path in files:
        rel = path.relative_to(plugin_root)
        for lineno, name in _private_names_cited_in_prose(path):
            cited.setdefault(name, []).append(f"{rel}:{lineno}")
    assert len(cited) >= 400, len(cited)

    unresolved = sorted(
        f"{name} ({', '.join(sites)})"
        for name, sites in cited.items()
        if name not in defined and name not in _PROSE_CITES_WITH_NO_DEFINITION
    )
    assert unresolved == [], (
        f"comment/docstring cite(s) naming no definition: {unresolved}. The "
        f"prose asserts something about code that is not in the tree, which "
        f"sends the next reader hunting for a function that does not exist "
        f"(D-217). Rename the cite to the symbol that does the work now -- and "
        f"if the name is genuinely one this tree removed and the comment "
        f"narrates that removal, record where it went in "
        f"_PROSE_CITES_WITH_NO_DEFINITION."
    )

    resurrected = sorted(
        name for name in _PROSE_CITES_WITH_NO_DEFINITION if name in defined
    )
    assert resurrected == [], (
        f"_PROSE_CITES_WITH_NO_DEFINITION entr(y/ies) excusing a LIVE name: "
        f"{resurrected}. Something defines it again, so the entry now excuses "
        f"a cite that needs no excuse -- and worse, it would vouch for that "
        f"definition to the reachability pin above. Delete the entry."
    )

    unused = sorted(set(_PROSE_CITES_WITH_NO_DEFINITION) - set(cited))
    assert unused == [], (
        f"_PROSE_CITES_WITH_NO_DEFINITION entr(y/ies) excusing nothing: "
        f"{unused}. No prose in these files cites the name any more, so the "
        f"entry outlived the comment it was written for. Delete it. Tolerating "
        f"a stale entry is how an allowlist becomes the place dead names go to "
        f"live."
    )




# --------------------------------------------------------------------------- #
# D-042 / CT-001 / AC-006 — a refusal over MCP names the offending property
# --------------------------------------------------------------------------- #


def _drive_mcp(name: str, arguments: dict):
    """Call a tool THROUGH THE MCP REQUEST HANDLER, not through `call_tool`.

    The distinction is the whole defect. `mcp.server.lowlevel.Server.call_tool`
    wraps the module-level handler and validates `arguments` against the
    advertised `inputSchema` BEFORE dispatching, so a test that calls
    `server.call_tool(...)` directly walks past the very layer that answered.
    Driving `request_handlers[CallToolRequest]` is the transport a client uses.
    """
    import asyncio

    from mcp import types

    import foundry_mcp.server as srv

    handler = srv.server.request_handlers[types.CallToolRequest]
    request = types.CallToolRequest(
        method="tools/call",
        params=types.CallToolRequestParams(name=name, arguments=arguments),
    )
    return asyncio.run(handler(request)).root.content[0].text




def test_the_advertised_enum_is_still_readable_by_list_tools():
    """The other half of the fix, and the constraint it had to respect.

    Taking validation back from the SDK must not take the VOCABULARY off the
    wire: `list_tools` is where a client learns which values are legal, and the
    C-11 contract says every enum this server advertises is `sorted()` over an
    imported vocab frozenset. A schema that stopped advertising the enum would
    trade a nameless refusal for an undocumented one.
    """
    import asyncio

    from foundry_mcp import server as srv
    from foundry_mcp.schemas.vocab import DEFECT_TIERS, FIX_AUTHORS

    tools = {t.name: t for t in asyncio.run(srv.list_tools())}
    defect = tools["Foundry-Defect"].inputSchema["properties"]
    assert defect["tier"]["enum"] == sorted(DEFECT_TIERS)
    fix = tools["Foundry-Fix"].inputSchema["properties"]
    assert fix["authored_by"]["enum"] == sorted(FIX_AUTHORS)




def test_call_tool_converts_an_unhandled_error_into_a_named_result():
    """The outermost net. Every handler is supposed to return named refusals,
    but this boundary had no try/except at all, so ONE unguarded read raised
    out of the MCP call itself.

    Driven through a tool name with no display formatter, so the assertion
    reads the RESULT rather than a formatter's rendering of it.
    """
    import asyncio

    import foundry_mcp.server as srv

    def _boom(_args):
        raise RuntimeError("exploded")

    srv._DISPATCH["Foundry-Boom-Test"] = _boom
    try:
        out = asyncio.run(srv.call_tool("Foundry-Boom-Test", {}))
    finally:
        del srv._DISPATCH["Foundry-Boom-Test"]

    payload = json.loads(out[0].text)
    assert "Foundry-Boom-Test failed" in payload["error"]
    assert "RuntimeError" in payload["error"]
    assert "exploded" in payload["error"]
    assert payload["hint"]




def test_call_tool_still_returns_a_normal_result_unwrapped():
    """The net must not change the happy path."""
    import asyncio

    import foundry_mcp.server as srv

    srv._DISPATCH["Foundry-Fine-Test"] = lambda _a: {"ok": True, "value": 42}
    try:
        out = asyncio.run(srv.call_tool("Foundry-Fine-Test", {}))
    finally:
        del srv._DISPATCH["Foundry-Fine-Test"]

    assert json.loads(out[0].text) == {"ok": True, "value": 42}




def _module_namespace(path: Path, tree: ast.Module) -> dict:
    """The names ``path``'s own module can see, for resolving exceptions.

    Three derived layers, no table:
      * builtins -- ``OSError``, ``ValueError``, ``UnicodeDecodeError``, ...
      * the real module object when it is importable, which is what resolves a
        handler naming an exception the module DEFINES or imports by name
        (``LedgerShapeError``)
      * the modules the file's own ``import X [as Y]`` statements name, which
        is what resolves the dotted ``json.JSONDecodeError`` spelling in a file
        that is not itself importable
      * the objects its ``from X import Y [as Z]`` statements bind, which is
        what resolves a bare ``loads`` or a bare ``JSONDecodeError`` in a file
        that is not itself importable (D-147: the alias was the spelling the
        load axis could not see)

    Every layer is read off the source or the package; none is typed here.
    """
    namespace = dict(vars(builtins))
    try:
        relative = path.resolve().relative_to(Path(foundry_mcp.__file__).resolve().parent)
        dotted = "foundry_mcp." + ".".join(relative.with_suffix("").parts)
        namespace.update(vars(importlib.import_module(dotted)))
    except Exception:
        pass
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                try:
                    namespace.setdefault(
                        alias.asname or alias.name.split(".")[0],
                        importlib.import_module(alias.name),
                    )
                except Exception:
                    pass
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            # Relative imports are skipped deliberately: they only occur inside
            # the package, where the importable-module layer above has already
            # bound every name the module can see. A `from . import x` here
            # would need the importing module's own package context, which is
            # exactly what that layer supplies.
            try:
                module = importlib.import_module(node.module)
            except Exception:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                try:
                    namespace.setdefault(
                        alias.asname or alias.name, getattr(module, alias.name)
                    )
                except AttributeError:
                    pass
    return namespace




def _handler_type_nodes(node: ast.AST) -> list[ast.AST]:
    """The exception expressions an ``except`` clause names, and only those.

    NOT ``ast.walk``: walking ``json.JSONDecodeError`` also yields the inner
    ``json`` Name, which resolves to a module rather than an exception and
    would be reported as an unrecognised handler name on a handler that is
    perfectly well-formed. A tuple is unpacked one level; anything else is one
    expression.
    """
    if isinstance(node, ast.Tuple):
        return list(node.elts)
    return [node]




def _handler_classes(
    handler: ast.ExceptHandler, namespace: dict
) -> tuple[list[type[BaseException]], list[str]]:
    """The exception CLASSES this ``except`` catches, and the names it could not.

    ``json.JSONDecodeError`` and a bare ``JSONDecodeError`` both resolve, the
    first through the module object the file imported, the second straight out
    of the namespace.
    """
    if handler.type is None:
        return [BaseException], []  # bare `except:` catches everything

    resolved: list[type[BaseException]] = []
    unresolved: list[str] = []
    for node in _handler_type_nodes(handler.type):
        if isinstance(node, ast.Attribute):
            owner_name = getattr(node.value, "id", "?")
            spelling = f"{owner_name}.{node.attr}"
            owner = namespace.get(owner_name)
            obj = getattr(owner, node.attr, None) if owner is not None else None
        elif isinstance(node, ast.Name):
            spelling, obj = node.id, namespace.get(node.id)
        else:
            spelling, obj = ast.dump(node), None
        if isinstance(obj, type) and issubclass(obj, BaseException):
            resolved.append(obj)
        else:
            unresolved.append(spelling)
    return resolved, unresolved




def _handler_body_statements(handler: ast.ExceptHandler) -> list[ast.AST]:
    """Every node in the handler's own body, nested definitions excluded.

    A ``raise`` inside a function DEFINED in the handler does not run when the
    handler runs, so descending into it would report a handler that converts
    nothing.
    """
    out: list[ast.AST] = []

    def walk(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)
            ):
                continue
            out.append(child)
            walk(child)

    for stmt in handler.body:
        out.append(stmt)
        walk(stmt)
    return out




def _handler_reraises(handler: ast.ExceptHandler) -> bool:
    """True when this handler's body raises, so the site still leaks.

    D-147's second gap. ``except Exception as e: raise Boom(...) from e``
    catches everything the load can raise and was therefore counted as
    COVERING it — while the operation still raises across the MCP boundary,
    only under a different type. A bare ``raise`` is the same fact stated more
    plainly. Either way the caller gets an exception, which is the property
    this rule exists to deny, so a converting handler covers nothing.
    """
    return any(
        isinstance(node, ast.Raise) for node in _handler_body_statements(handler)
    )




def _uncovered_by(
    handlers: list[ast.ExceptHandler],
    namespace: dict,
    raises: tuple[type[BaseException], ...] = _DOCUMENT_LOAD_RAISES,
) -> tuple[list[str], list[str]]:
    """Which of a document load's raises these handlers leave uncaught.

    Returns ``(uncovered exception names, unresolvable/converting handler
    spellings)``. A handler that RE-RAISES contributes nothing to ``caught``
    and announces itself in the second list, exactly as an unresolvable
    handler name does — the two are the same failure from the caller's seat:
    an exception still crosses the boundary.
    """
    caught: list[type[BaseException]] = []
    unresolved: list[str] = []
    for handler in handlers:
        classes, missing = _handler_classes(handler, namespace)
        unresolved.extend(missing)
        if _handler_reraises(handler):
            unresolved.append(
                "re-raises: "
                + (ast.unparse(handler.type) if handler.type is not None else "except:")
            )
            continue
        caught.extend(classes)
    uncovered = [
        raised.__name__
        for raised in raises
        if not any(issubclass(raised, caught_cls) for caught_cls in caught)
    ]
    return uncovered, sorted(set(unresolved))




def _reads_a_file(node: ast.AST) -> bool:
    """True when the expression subtree reads a file's bytes or text."""
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        if isinstance(n.func, ast.Attribute) and n.func.attr in _FILE_READ_METHODS:
            return True
        if isinstance(n.func, ast.Name) and n.func.id == "open":
            return True
    return False



_UNRESOLVED = object()




def _resolve_dotted(node: ast.AST, namespace: dict) -> tuple[object, str]:
    """``(object, spelling)`` for a name or an attribute chain of any depth.

    The same three-layer namespace the handler axis resolves against, so
    ``json.loads``, ``j.loads`` (aliased import) and a bare ``loads``
    (``from json import loads``) all land on the same function object.
    """
    if isinstance(node, ast.Name):
        return namespace.get(node.id, _UNRESOLVED), node.id
    if isinstance(node, ast.Attribute):
        owner, spelling = _resolve_dotted(node.value, namespace)
        spelling = f"{spelling}.{node.attr}"
        if owner is _UNRESOLVED:
            return _UNRESOLVED, spelling
        return getattr(owner, node.attr, _UNRESOLVED), spelling
    return _UNRESOLVED, type(node).__name__




def _document_loader_call(node: ast.AST, namespace: dict) -> tuple[bool, str | None]:
    """``(is a document loader call, unresolved spelling)`` for one node.

    ``is a document loader call`` is true when the callee RESOLVES to one of
    ``_DOCUMENT_LOADERS``. A callee this resolver cannot see, whose last
    segment is nonetheless a loader's name, is ALSO treated as a member and
    carries its spelling out to the report — the conservative direction, and
    the same contract the handler axis holds: an unrecognised member announces
    itself rather than quietly widening the gap.
    """
    if not isinstance(node, ast.Call):
        return False, None
    obj, spelling = _resolve_dotted(node.func, namespace)
    if obj is not _UNRESOLVED:
        return any(obj is loader for loader in _DOCUMENT_LOADERS), None
    if spelling.rsplit(".", 1)[-1] in _LOADER_SEGMENTS:
        return True, spelling
    return False, None




def _is_document_load(node: ast.AST, namespace: dict) -> bool:
    """True for a resolved ``json.load(s)`` applied to something read off disk."""
    is_loader, _ = _document_loader_call(node, namespace)
    return is_loader and _reads_a_file(node)




def _walk_guarded(node: ast.AST, fn: str, handlers: tuple, visit) -> None:
    """Depth-first walk carrying the enclosing function and its ``except`` clauses.

    ``handlers`` accumulates the handlers of every enclosing ``try`` whose BODY
    this node sits in. It resets at every function boundary -- a function
    defined inside a ``try`` is not protected at the point it is CALLED -- and
    it does not extend into the handlers, ``else`` or ``finally``, where a
    second raise would propagate.

    D-137: this carried a BOOLEAN ("some enclosing handler named something from
    a list") and that is precisely what could not tell `except
    json.JSONDecodeError` from `except ValueError`. Carrying the handlers
    themselves lets the caller ask what they actually catch.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        fn, handlers = node.name, ()
    elif isinstance(node, (ast.Try, ast.TryStar)):
        inner = handlers + tuple(node.handlers)
        for stmt in node.body:
            _walk_guarded(stmt, fn, inner, visit)
        for part in (*node.handlers, *node.orelse, *node.finalbody):
            _walk_guarded(part, fn, handlers, visit)
        return
    visit(node, fn, handlers)
    for child in ast.iter_child_nodes(node):
        _walk_guarded(child, fn, handlers, visit)




def _is_file_read(node: ast.AST) -> bool:
    """True for the CALL that reads a file, not for every node above it."""
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Attribute) and node.func.attr in _FILE_READ_METHODS:
        return True
    return isinstance(node.func, ast.Name) and node.func.id == "open"




def _unguarded_document_loads(path: Path) -> tuple[list[str], list[str]]:
    """``(reads seen, offenders)`` for one module.

    D-130's shape, derived from the site rather than from a list of known
    copies: `json.loads(path.read_text(...))` whose enclosing handlers do not
    answer every exception that expression can raise. Route the read through
    the canonical `foundry_state.read_document` -- which closes OSError,
    UnicodeDecodeError and JSONDecodeError in ONE call and NAMES THE FILE --
    rather than re-deciding the raise set at each site.

    Each offender carries the exceptions it leaks, so the report says what is
    wrong rather than only where.

    D-142 — WHAT THE FIRST ELEMENT IS FOR. This used to return offenders
    alone, so `assert not offenders` was green both when the package was clean
    AND when the recogniser had silently stopped recognising the package's
    spelling. The members of this class are the READS: "the read and the
    decode are one operation" is the sentence the whole rule is built on, so
    every file read is a site this rule has an opinion about, and
    ``foundry_state.py#read_text_file`` -- where all fourteen D-137 sites
    converged -- must appear in it or the derivation has gone blind.

    D-153 — THE RULE HAS TWO RECOGNISERS AND ONLY ONE OF THEM WAS ANCHORED.
    "The read and the decode are one operation" cuts both ways: this rule
    decides membership on a READ axis (``_is_file_read`` / ``_reads_a_file``)
    AND on a LOAD axis (``_document_loader_call`` / ``_resolve_dotted``), and
    ``seen`` was keyed on the read axis alone. Blind the LOAD recogniser --
    monkeypatch ``_document_loader_call`` to ``(False, None)``, which is what a
    package that drifts to orjson, or to a wrapper this resolver cannot import,
    does to it -- and ``seen`` stayed full of reads, ``offenders`` emptied
    because no site was classified as a load at all, and the rule reported a
    clean package while seeing zero of the operation it polices. The plant
    tests do not close it: a plant proves the recogniser sees a PLANTED
    spelling, not that it still sees the REAL package's. So BOTH axes report
    into ``seen``, and each anchor names a site only its own axis can produce:
    ``foundry_state.py#read_text_file`` reads and never loads,
    ``foundry_state.py#read_json`` loads and never reads (its read is one call
    down, inside ``read_text_file``). Blinding either axis now empties its own
    anchor and the rule fails by name.

    D-141/D-147 — THE READ AND THE LOAD NEED NOT SHARE AN EXPRESSION. A name
    bound to a file read and handed to a loader two statements later is the
    same operation spelled apart, and each rung is judged against the handlers
    that enclose IT: the read must answer OSError + UnicodeDecodeError where
    it sits, the decode must answer JSONDecodeError where IT sits. A union
    would call `except FileNotFoundError` + `except json.JSONDecodeError` a
    covered pair, which is verbatim the shape
    ``validate-test-observations.py`` held.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen: list[str] = []
    offenders: list[str] = []
    namespace = _module_namespace(path, tree)
    # function -> {name bound to a file read: the handlers enclosing that read}
    read_bindings: dict[str, dict[str, tuple]] = {}

    def report(
        fn: str,
        lineno: int,
        uncovered: list[str],
        unresolved: list[str],
        spelling: str | None,
    ) -> None:
        if not uncovered:
            return
        note = f" [unresolved handler names: {sorted(set(unresolved))}]" if unresolved else ""
        # Carried in its OWN bracket, because "this handler names something I
        # cannot resolve" and "this CALLEE is something I cannot resolve" are
        # different unrecognised members and an operator reading the report
        # must not have to guess which axis went blind.
        note += f" [unresolved load spelling: {spelling}]" if spelling else ""
        offenders.append(
            f"{path.name}::{fn}:{lineno} leaks {'+'.join(uncovered)}{note}"
        )

    def visit(node: ast.AST, fn: str, handlers: tuple) -> None:
        if _is_file_read(node):
            seen.append(f"{path.name}#{fn}")
        if isinstance(node, ast.Assign) and _reads_a_file(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    read_bindings.setdefault(fn, {})[target.id] = handlers
            return
        is_loader, spelling = _document_loader_call(node, namespace)
        if not is_loader:
            return
        # D-153: the LOAD axis reports its members too, whether or not this
        # particular load turns out to be a DOCUMENT load. What can go blind is
        # the recogniser -- "is this callee a loader" -- and it is that question
        # the anchor has to be able to see answered `yes` somewhere in the real
        # package. Which loads are then judged is the rule; which loads are SEEN
        # is the derivation, and the two must be separately observable.
        seen.append(f"{path.name}#{fn}")
        if _reads_a_file(node):
            uncovered, unresolved = _uncovered_by(list(handlers), namespace)
            report(fn, node.lineno, uncovered, unresolved, spelling)
            return
        bound = read_bindings.get(fn, {})
        argument = node.args[0] if node.args else None
        if not (isinstance(argument, ast.Name) and argument.id in bound):
            return  # a JSONL line, a subprocess's stdout — not a DOCUMENT load
        read_uncovered, read_unresolved = _uncovered_by(
            list(bound[argument.id]), namespace, _READ_RAISES
        )
        decode_uncovered, decode_unresolved = _uncovered_by(
            list(handlers), namespace, _DECODE_RAISES
        )
        report(
            fn,
            node.lineno,
            read_uncovered + decode_uncovered,
            read_unresolved + decode_unresolved,
            spelling,
        )

    _walk_guarded(tree, "<module>", (), visit)
    return sorted(set(seen)), sorted(set(offenders))




def _is_artifact_rename(node: ast.AST, namespace: dict) -> bool:
    """True when this call IS a move primitive — the rename rule's membership.

    `Path.rename` is a method on a value whose type is unknowable at parse
    time, so the attribute name is all there is to match. The MODULE-level
    primitives are not: D-147's axis applied here, they are resolved through
    the namespace and compared as objects, so `import os as o` +
    `o.replace(tmp, path)` is a member. The `os.` string check survives only as
    the fallback for a module this resolver cannot import, because a bare
    `.replace` cannot be promoted -- `text.replace("a", "b")` would be every
    second line.

    D-153 — LIFTED OUT OF THE LOOP SO IT CAN BE BLINDED ON ITS OWN. This lived
    inline inside the scan, which meant the only taint a test could apply was
    to the WHOLE scan (`lambda p: ([], [])`). That proves the rule notices an
    empty result; it does not prove the rule notices its RECOGNISER going
    blind while the scan still runs, which is the failure D-153 was filed on
    one rule over. Every rule in this file now names its membership recogniser,
    and `test_a_blind_recogniser_fails_its_own_rule_by_name` drives each.
    """
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    resolved, _spelling = _resolve_dotted(node.func, namespace)
    is_rename = node.func.attr == "rename" or any(
        resolved is primitive for primitive in _RENAME_PRIMITIVES
    )
    is_replace = (
        resolved is _UNRESOLVED
        and node.func.attr == "replace"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "os"
    )
    return is_rename or is_replace




def _unlocked_artifact_renames(path: Path) -> tuple[list[str], list[str]]:
    """``(renames seen, offenders)`` — the tmp+rename sites, and the unlocked ones.

    D-103's shape: `tmp = path.with_suffix(".tmp"); tmp.write_text(...);
    tmp.rename(path)`. The sidecar name is SHARED by every concurrent writer of
    the same artifact, so a peer's rename can move this call's half-written
    payload into place or delete it mid-write. A module that renames onto a
    shared artifact must serialize its writes; one that does not must not carry
    its own copy of the write primitive at all -- it imports the guarded one.

    Derived from whether the module itself takes an exclusive lock, not from
    which modules are known to be safe.

    D-142: the rename sites are collected WHETHER OR NOT the module locks, and
    the lock only decides which of them offend. The old shape returned ``[]``
    the moment it saw a ``flock`` anywhere in the file, so the two modules that
    carry the write primitive -- the very ones whose rename idiom this rule
    tracks -- contributed nothing the caller could anchor on, and a rename
    hoisted behind a helper would have emptied the scan in silence.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    namespace = _module_namespace(path, tree)
    holds_a_lock = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "flock"
        for n in ast.walk(tree)
    )

    seen: list[str] = []
    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not _is_artifact_rename(node, namespace):
                continue
            seen.append(f"{path.name}#{fn.name}")
            if not holds_a_lock:
                offenders.append(f"{path.name}::{fn.name}:{node.lineno}")
    return sorted(set(seen)), sorted(set(offenders))




def _iteration_sources(node: ast.AST) -> list[ast.AST]:
    """The iterables of a ``for`` loop or any comprehension."""
    if isinstance(node, (ast.For, ast.AsyncFor)):
        return [node.iter]
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        return [gen.iter for gen in node.generators]
    return []




def _opens_a_ledger_transaction(func: ast.AST, namespace: dict) -> bool:
    """True when this callee IS the locked ledger primitive.

    D-147's axis, applied to the third rule that matched a spelling by string.
    The callee is resolved through the module's own namespace and compared as
    an OBJECT, so ``from foundry_mcp.tools.foundry import ledger_transaction as
    _tx`` is a member of this rule the day it is written. A callee this
    resolver cannot see (a synthetic module under tmp_path, or one that will
    not import) falls back to the bare name -- the conservative direction, and
    the only one available when there is no namespace to ask.
    """
    from foundry_mcp.tools.foundry import ledger_transaction

    resolved, spelling = _resolve_dotted(func, namespace)
    if resolved is not _UNRESOLVED:
        return resolved is ledger_transaction
    return spelling.rsplit(".", 1)[-1] == ledger_transaction.__name__




def _raw_ledger_iterations(path: Path) -> tuple[list[str], list[str]]:
    """``(transaction bindings seen, offenders)`` for one module.

    D-128's shape. ``ledger_transaction`` USED to yield the ledger's list
    verbatim, malformed historical records included, and
    ``allocate_record_id`` was the only reader that skipped a non-dict. Every
    other scan assumed dicts, so `d.get("status")` raised on one -- and it
    raised AFTER the new record was appended, so the transaction aborted,
    nothing was written, and the filing the stream made was GONE while the
    caller got a traceback that named no file.

    D-127 has since moved that filter INSIDE the primitive: the transaction
    now yields mapping records only and re-inserts the non-dicts by index
    before the write. That is the better fix and it is not this one's
    substitute. This rule asserts the ORCHESTRATOR still reaches records
    through `_dict_records` rather than resting on what a sibling module
    currently does inside a contextmanager -- so a revert there, a second
    ledger primitive, or a caller binding the raw document surfaces here as a
    named offender instead of as a silently reopened D-128.

    The rule is the ITERATION, not the attribute access: `_dict_records(...)`
    is the one place that tolerance is named, so a scan that iterates the
    binding directly has bypassed it however carefully its body is written. An
    inline `isinstance(d, dict)` is a hand-applied copy of the primitive --
    correct today, and the same copy-per-site that is this whole class.

    Whole-list operations (``append``, ``len``, ``allocate_record_id``) do not
    touch elements and are untouched by this rule.

    D-142: the FIRST element is every function this scan recognised as opening
    a transaction at all. ``foundry_sync_defects`` and
    ``foundry_mark_defect_fixed`` -- the two sites D-128 was filed on -- still
    open one, so they must appear there; if the ``with ledger_transaction(...)
    as records`` idiom moves behind a helper the scan sees nothing, and
    ``assert not offenders`` alone would call that clean.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    namespace = _module_namespace(path, tree)
    seen: list[str] = []
    offenders: list[str] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for block in ast.walk(fn):
            if not isinstance(block, (ast.With, ast.AsyncWith)):
                continue
            bindings = {
                item.optional_vars.id
                for item in block.items
                if isinstance(item.context_expr, ast.Call)
                and isinstance(item.optional_vars, ast.Name)
                and _opens_a_ledger_transaction(item.context_expr.func, namespace)
            }
            if not bindings:
                continue
            seen.append(f"{path.name}#{fn.name}")
            for inner in ast.walk(block):
                for source in _iteration_sources(inner):
                    if isinstance(source, ast.Name) and source.id in bindings:
                        offenders.append(f"{path.name}::{fn.name}:{source.lineno}")
    return sorted(set(seen)), sorted(set(offenders))




def _package_call_graph(modules: list[Path]) -> tuple[dict, dict, dict]:
    """``(callees, decorators, defining module)`` for every function in the package.

    Keyed by bare function name, which is what a cross-module call looks like
    at the AST — the package has no duplicate top-level function names, and a
    collision would only make this STRICTER (it would union the callees).
    """
    callees: dict[str, set[str]] = {}
    decorators: dict[str, set[str]] = {}
    where: dict[str, str] = {}
    for path in modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = set()
            for node in ast.walk(fn):
                if isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name):
                        names.add(node.func.id)
                    elif isinstance(node.func, ast.Attribute):
                        names.add(node.func.attr)
            callees.setdefault(fn.name, set()).update(names)
            decorators.setdefault(fn.name, set()).update(
                d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", "")
                for d in fn.decorator_list
            )
            where.setdefault(fn.name, path.name)
    return callees, decorators, where




def _dispatched_functions(server_src: Path) -> dict[str, str]:
    """``{function name: tool name}`` for every entry in server.py's _DISPATCH."""
    tree = ast.parse(server_src.read_text(encoding="utf-8"))
    dispatch = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_DISPATCH" for t in node.targets
        ):
            dispatch = node.value
    assert isinstance(dispatch, ast.Dict), "server.py has no _DISPATCH dict"
    doors: dict[str, str] = {}
    for key, value in zip(dispatch.keys, dispatch.values):
        for sub in ast.walk(value):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name):
                doors[sub.func.id] = key.value
    return doors




def test_every_dispatched_ledger_writer_in_the_package_answers_in_band():
    """D-127's structural question, asked of the WHOLE PACKAGE.

    ``foundry.py``'s own test derives its door set from ``server.py``'s
    ``_DISPATCH`` crossed with THAT MODULE's call graph — so it was blind to
    exactly the module D-127 was filed on. ``ledger_shape_problem``'s docstring
    named ``foundry_orchestrator``'s two ledger writers as its consumers and
    they had never called it; grepping the whole plugin tree returned its own
    definition and one in-module call site, and nothing else. A NEW export with
    a NEW docstring naming two consumers had zero of them.

    The rule is about REACHABILITY, not about who literally types
    ``ledger_transaction``: a dispatched tool that can reach the locked
    primitive by any path must carry the decorator that converts its raise.
    An internal helper does NOT need one — ``foundry.py``'s
    ``record_denylist_tripwire`` opens a transaction and is reached only from
    doors that are decorated, which the transitive walk below establishes
    rather than assumes. This scan found it, which is the argument for the
    scan: it is precisely the kind of caller a hand search does not turn up.
    """
    import foundry_mcp.server as foundry_server

    pkg = Path(foundry_mcp.__file__).resolve().parent
    modules = _package_modules(pkg)
    callees, decorators, where = _package_call_graph(modules)
    doors = _dispatched_functions(Path(foundry_server.__file__).resolve())

    def reaches_a_transaction(name: str, seen: set | None = None) -> bool:
        seen = seen if seen is not None else set()
        if name in seen:
            return False
        seen.add(name)
        sub = callees.get(name, set())
        if "ledger_transaction" in sub:
            return True
        return any(reaches_a_transaction(c, seen) for c in sub if c in callees)

    writing_doors = {n: t for n, t in doors.items() if reaches_a_transaction(n)}
    # The derivation must SEE the doors, or the assertion below is vacuous —
    # and it must see them in BOTH modules, which is the widening D-127 needs.
    assert writing_doors, "no dispatched tool reaches a locked transaction"
    assert {where[n] for n in writing_doors} >= {"foundry.py", "fix_gate.py"}, {
        n: where[n] for n in writing_doors
    }

    offenders = sorted(
        f"{tool} -> {where[name]}::{name}"
        for name, tool in writing_doors.items()
        if _LEDGER_REFUSAL_DECORATOR not in decorators.get(name, set())
    )
    assert not offenders, (
        f"{offenders} can reach a ledger_transaction, so a LedgerShapeError "
        f"raised inside the locked primitive escapes across the MCP boundary, "
        f"where call_tool turns it into an unhandled-error banner instead of "
        f"the house {{error, hint}} refusal. That is D-127: the right rule "
        f"living in one copy, with a docstring naming the consumers that were "
        f"never wired. Decorate with @ledger_refusals."
    )




@pytest.mark.parametrize("container", _BAD_CONTAINERS)
@pytest.mark.parametrize("tool", sorted(_ORCHESTRATOR_LEDGER_DOORS))
def test_both_orchestrator_ledger_doors_refuse_in_band(run_env, monkeypatch, tool, container):
    """D-127 — the two doors whose docstring named them and never wired them.

    ``ledger_shape_problem``'s docstring said its second consumer was
    "``foundry_orchestrator``'s two ledger writers"; grep over the whole plugin
    tree returned its own definition and ONE in-module call site. So the
    orchestrator doors hit ``ledger_transaction``'s backstop raise, and
    ``server.py#call_tool``'s outer net turned it into the banner
    "Foundry-Sync failed: LedgerShapeError: ..." — which carries the filename
    but ships as an UNHANDLED ERROR, not the house {error, hint} refusal every
    other failure on this surface returns. That refusal shape is the precise
    thing D-095/D-096 were filed to establish.

    Mitigating and stated as the ledger states it: it failed CLOSED, so this
    also asserts the file is byte-identical afterward. It is a refusal-shape
    defect, not a data-loss one — which is exactly why only a parity test
    catches it.
    """
    from foundry_mcp import server as srv

    project_root, fdir = run_env
    # The _DISPATCH lambdas read server._project_root, NOT args["project_root"]
    # — so driving them without this writes to the AMBIENT run directory.
    monkeypatch.setattr(srv, "_project_root", project_root)
    defects = fdir / "defects.json"
    defects.write_text(
        json.dumps({"defects": container, "meta": "KEEP"}), encoding="utf-8"
    )
    before = defects.read_bytes()

    result = srv._DISPATCH[tool](dict(_ORCHESTRATOR_LEDGER_DOORS[tool]))

    assert isinstance(result, dict) and "error" in result, result
    assert "defects.json" in result["error"], result["error"]
    assert result.get("hint"), result
    # Failed closed: the sibling key that proved the write completed in D-096
    # is still there, and nothing was rewritten.
    assert defects.read_bytes() == before




@pytest.mark.parametrize("container", _BAD_CONTAINERS)
def test_the_two_doors_tell_one_story_about_one_broken_ledger(run_env, monkeypatch, container):
    """Parity, which is the whole point of a cross-door test.

    An operator must not be able to tell WHICH door noticed, nor get a
    different account of the same file from each. D-094's parity shape.
    """
    from foundry_mcp import server as srv

    project_root, fdir = run_env
    monkeypatch.setattr(srv, "_project_root", project_root)
    answers = set()
    for tool, args in sorted(_ORCHESTRATOR_LEDGER_DOORS.items()):
        (fdir / "defects.json").write_text(
            json.dumps({"defects": container, "meta": "KEEP"}), encoding="utf-8"
        )
        result = srv._DISPATCH[tool](dict(args))
        answers.add((result["error"], result["hint"]))
    assert len(answers) == 1, answers




def test_no_document_load_can_raise_across_the_mcp_boundary():
    """D-130, asserted as a property of BOTH shipped source trees.

    Before the fix this reported forge_spec.py::_load_json:32 -- the third
    `_load_json` copy, reachable over MCP through Forge-Spec-Start/Check/Status
    -- plus four more the defect report had not found, every one of them on the
    request path: foundry_handoff.py::foundry_spec_hash (Foundry-Spec-Hash),
    foundry_validate.py::foundry_validate_castings x2
    (Foundry-Validate-Castings) and validation.py::validate_report
    (Validate-Report). That the scan found four the hand search missed is the
    argument for the scan.

    D-141 — AND THE ROOT WAS THE NEXT PLACE THE CLASS WENT. The corpus was
    `Path(foundry_mcp.__file__).parent`, the installed package alone, so the
    three CLIs in `plugins/foundry/scripts/` were never asked. All three held
    the majority D-137 spelling and two of them were driven raising
    UnicodeDecodeError on one non-UTF-8 byte. The corpus is now the same two
    trees D-134's manifest rule already derives, imported from it so the two
    cannot disagree.
    """
    modules = _scanned_modules()
    assert modules, f"no modules discovered under {_scanned_roots()}"

    # THE ROOT ANCHOR. Every member of this class now routes through the
    # canonical primitive, so the scripts tree contributes no members of its
    # own -- and a corpus that silently narrowed back to the package would
    # therefore look identical from the member side alone. Name the tree.
    scripts_root = _scanned_roots()[-1]
    assert scripts_root.is_dir() and scripts_root.name == "scripts", scripts_root
    in_scripts = sorted(p.name for p in modules if p.parent == scripts_root)
    assert {
        "measure-run.py",
        "migrate-archive.py",
        "validate-test-observations.py",
    } <= set(in_scripts), (
        f"the corpus reaches {in_scripts} under {scripts_root}, which does not "
        f"include the three shipped CLIs D-141 was filed on. The root "
        f"derivation has narrowed and this rule is no longer asked about the "
        f"tree the run actually ships."
    )

    seen, offenders = _scan(modules, _unguarded_document_loads)
    # THE MEMBER ANCHOR. The members of this class are the READS -- "the read
    # and the decode are one operation" is the sentence the rule is built on.
    # All fourteen D-137 sites converged on `foundry_state.read_text_file`, so
    # that is the site this derivation must still recognise; if
    # `_FILE_READ_METHODS` or `_is_file_read` stops matching the package's
    # spelling, `seen` empties and this fails by name instead of the offender
    # assertion passing for the wrong reason (D-142).
    assert "foundry_state.py#read_text_file" in seen, (
        f"the scan recognised {len(seen)} document reads and none of them is "
        f"the canonical primitive every guarded site routes through, so the "
        f"read recogniser has gone blind and the assertion below is vacuous: "
        f"{seen}"
    )
    # THE LOAD ANCHOR (D-153). The rule polices document LOADS, and until now
    # `seen` was keyed on READS alone -- so blinding `_document_loader_call`
    # left `seen` full, `offenders` empty, and this test GREEN over a package in
    # which the scan classified not one site as a load. `read_json` is the
    # primitive every guarded load in the package routes through and it does not
    # read a file itself (its read is one call down, in `read_text_file`), so it
    # is a member ONLY the load axis can produce. If the package's load spelling
    # drifts past what `_document_loader_call` / `_resolve_dotted` recognise --
    # orjson, a wrapper the resolver cannot import, a loader that is not
    # `json.load(s)` by object identity -- this empties and says so.
    assert "foundry_state.py#read_json" in seen, (
        f"the scan classified no site in the package as a document LOAD, so the "
        f"load recogniser has gone blind: every module reads clean because "
        f"nothing is a load, not because nothing leaks. `read_json` is the "
        f"primitive every guarded load routes through and it must be in here. "
        f"Fix `_document_loader_call` / `_resolve_dotted` -- do not weaken this "
        f"anchor. Saw: {seen}"
    )

    assert not offenders, (
        f"{offenders} load a JSON document off disk without handling the decode "
        f"failure, so a corrupt or truncated artifact raises out of the tool "
        f"and across the MCP boundary as a traceback that names no file. "
        f"NFR-002 and the house rule are the same sentence here: never raise "
        f"across MCP, refuse by name. Route the read through the module's "
        f"tolerant `_load_json` and report the file with `_artifact_guard` / "
        f"`_document_problem`. There is no allow-list to add yourself to -- "
        f"the guarded loaders pass this rule on their own merits."
    )




def test_a_planted_loader_under_the_scripts_root_is_reported(tmp_path):
    """D-141 adjacent-path test: a NEW file in the SECOND tree is a member.

    The path the defect was filed on is "an existing CLI in
    plugins/foundry/scripts". The adjacent path this drives is a file that
    does not exist yet, in that same non-importable tree -- the position the
    package-only root could never reach. Hermetic: it builds a synthetic
    two-root layout under tmp_path rather than writing into the repo, because
    two other castings are running this suite against the real tree
    concurrently.

    The claim this makes together with the root anchor above: the corpus is
    `rglob("*.py")` over both derived roots, that derivation demonstrably
    reaches the real scripts directory, and the recogniser reports the shape
    wherever it is written -- hyphenated, non-importable filename included.
    """
    scripts = tmp_path / "plugins" / "foundry" / "scripts"
    scripts.mkdir(parents=True)
    planted = scripts / "brand-new-cli.py"
    planted.write_text(_PLANTED_OSERROR_DECODE_LOADER, encoding="utf-8")

    modules = sorted(scripts.rglob("*.py"))
    assert modules == [planted], modules
    seen, offenders = _scan(modules, _unguarded_document_loads)
    assert offenders == ["brand-new-cli.py::_load_manifest:5 leaks UnicodeDecodeError"]
    assert seen == ["brand-new-cli.py#_load_manifest"]




def test_no_module_renames_onto_a_run_artifact_without_a_lock():
    """D-103, as a package property.

    Before the fix: forge_spec.py::_save_json:38 and
    intent_coverage.py::_save_json_atomic:98, both the shared-sidecar shape,
    the second of which writes castings/manifest.json -- a real run artifact
    two other tools read concurrently. Its own docstring claimed to mirror
    foundry.py's discipline; it mirrored the tmp+rename and not the flock.
    """
    pkg = Path(foundry_mcp.__file__).resolve().parent
    seen, offenders = _scan(_package_modules(pkg), _unlocked_artifact_renames)
    # D-142's anchor: the two modules that OWN the write primitive must be
    # recognised as renaming, or the rename idiom has moved behind a spelling
    # this scan no longer tracks and `assert not offenders` means nothing.
    # fallout FR-004: `_save_json` moved to `tools/artifacts.py` when commit
    # group (0) deleted the monolith's copy of Block B. The anchor follows the
    # DEFINITION, because what it pins is that the scan still recognises the
    # idiom -- not which file happens to hold it.
    assert {
        "foundry.py#_atomic_rename_write",
        "artifacts.py#_save_json",
    } <= set(seen), (
        f"the scan recognised {seen} as tmp+rename sites, which does not "
        f"include the package's own two write primitives. The rename "
        f"derivation has gone blind, so the offender assertion below is "
        f"vacuous."
    )
    assert not offenders, (
        f"{offenders} rename a tmp sidecar into place in a module that takes no "
        f"exclusive lock. The sidecar name is shared by every concurrent writer "
        f"of the same artifact, so a peer's rename moves a half-written payload "
        f"into place or deletes it mid-write. Import the guarded `_save_json` / "
        f"`_document_transaction` rather than carrying a fifth copy of the "
        f"write primitive."
    )




def test_no_ledger_scan_bypasses_the_malformed_record_filter():
    """D-128, as a package property.

    Before the fix this reported foundry_sync_defects:3631,3644,3796 (the batch
    door D-097 missed) and foundry_mark_defect_fixed:3260,3357 (correct today
    via an inline `isinstance`, which is the hand-applied copy this class is
    made of).
    """
    pkg = Path(foundry_mcp.__file__).resolve().parent
    seen, offenders = _scan(_package_modules(pkg), _raw_ledger_iterations)
    # D-142's anchor: the two doors D-128 was filed on still open transactions,
    # so the scan must still see them. It sees them through the
    # `with ledger_transaction(...) as records` idiom alone; move that behind a
    # helper and the scan empties while every planted copy still passes.
    assert {
        "fix_gate.py#foundry_sync_defects",
        "fix_gate.py#foundry_mark_defect_fixed",
    } <= set(seen), (
        f"the scan recognised {seen} as ledger-transaction openers, which does "
        f"not include the two doors this defect was filed on. The derivation "
        f"has gone blind, so the offender assertion below is vacuous."
    )
    assert not offenders, (
        f"{offenders} iterate a ledger_transaction record binding directly. The "
        f"transaction yields the ledger VERBATIM, malformed historical records "
        f"included, so an element scan raises mid-transaction -- after the "
        f"append, so nothing is written and the filing is silently lost. "
        f"Iterate `_dict_records(<binding>)`; an inline isinstance is another "
        f"copy of the same filter, which is the defect class itself."
    )




def test_the_document_suffix_table_covers_every_declared_run_artifact():
    """FR-026 / D-197 / D-201: the table is a membership list, so its hole is
    watched — on BOTH of the axes `_is_document_position` asks.

    `_RUN_DOCUMENT_SUFFIXES` says which TYPES a reader opens and
    `_RUN_MARKER_NAMES` says which suffix-less NAMES it opens; an artifact
    absent from the axis its own name falls on is a directory position the
    guard walks past. That hole is accepted (the inverse refuses on
    `.dist-info`, of which this repo's virtualenvs hold thirty) — but it is not
    left unwatched: the expected set is DERIVED from the `*_FILENAME` and
    `*_MARKER` constants this package declares, so declaring a run artifact
    fails here the day it is declared rather than the cycle someone notices a
    door acting on an empty document.

    D-201 — THE GUARD CLAUSE EXEMPTED EXACTLY WHAT WENT WRONG. This read
    `if (suffix := Path(value).suffix.lower()) and suffix not in ...`, and the
    leading truthiness test made every suffix-less declared name skip the
    assertion entirely. So the pin covered the axis that was already fixed and
    structurally could not see the one that was not: sixteen sentinels the
    package writes and reads back were exempt from their own coverage test. A
    guard clause that drops a subject is not a narrower check, it is a blind
    spot, and it must route to the other axis instead of returning early.
    """
    import ast

    plugin_root = Path(artifacts.__file__).resolve().parents[4]
    assert plugin_root.name == "foundry", plugin_root

    declared: dict[str, str] = {}
    scanned = 0
    for path in sorted(plugin_root.rglob("*.py")):
        if not _INSTALLED_DEPENDENCY_DIRS.isdisjoint(path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, SyntaxError):
            continue
        scanned += 1
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if not (isinstance(node.value, ast.Constant)
                    and isinstance(node.value.value, str)):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                value = node.value.value
                # `_FILENAME` means a file, unambiguously, whatever the value.
                # `_MARKER` does NOT: this package also spells text sentinels
                # that way (`RESULT_JSON_MARKER`, `_CLASS_RULE_MARKER`,
                # `_ID_LITERAL_MARKER`), and demanding those be run artifacts
                # would make the pin fail on things no reader ever opens. What
                # separates the two is a property of run sentinels rather than
                # a list of exceptions: every one of them is a DOTFILE BASENAME
                # -- a leading "." and no path separator -- and no text marker
                # is. A run artifact that is suffix-less and dot-less is still
                # covered, because a `_FILENAME` constant is never filtered.
                is_run_artifact = target.id.endswith("_FILENAME") or (
                    target.id.endswith("_MARKER")
                    and value.startswith(".")
                    and Path(value).name == value
                )
                if is_run_artifact:
                    declared[target.id] = value
    # Derivations that silently found nothing would pass forever, PER AXIS: a
    # single total stays green while either half collapses to zero -- which is
    # D-201's own shape, a guard clause quietly emptying one side of the check.
    assert scanned >= 50, scanned
    suffixed = {n: v for n, v in declared.items() if Path(v).suffix}
    markers = {n: v for n, v in declared.items() if not Path(v).suffix}
    assert len(suffixed) >= 10, suffixed
    assert len(markers) >= 13, markers

    unenrolled = sorted(
        f"{name} = {value!r}"
        for name, value in declared.items()
        if not artifacts._is_document_position(Path("run") / value)
    )
    assert unenrolled == [], (
        f"run artifact(s) neither _RUN_DOCUMENT_SUFFIXES nor _RUN_MARKER_NAMES "
        f"knows: {unenrolled}. A directory occupying one of those names is "
        f"walked past by _is_document_position, so every door acts on a "
        f"fabricated empty document and reports success (D-140, D-197, D-201)."
    )




def _plant(tmp_path: Path, name: str, source: str) -> Path:
    module = tmp_path / name
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source, encoding="utf-8")
    return module




def render_derived_guard_table(tmp_path: Path) -> str:
    """The structural claim: a FIFTH copy is caught wherever it is written.

    Membership is derived over the package on both axes, so the planted copies
    go in positions a directory literal would have missed — the package ROOT
    (server.py's position, which owns _DISPATCH) and a NON-tools subpackage.

    D-141 adds the SECOND SHIPPED TREE to the same picture, and D-147 adds the
    two load spellings the string match could not see. Each row prints what
    the scan SAW as well as what it reported, because a rule that saw nothing
    is the failure D-142 was filed on and it must not read as a clean row.
    """
    decode_seen, decode_offenders = _scan(_scanned_modules(), _unguarded_document_loads)
    pkg = Path(foundry_mcp.__file__).resolve().parent
    rename_seen, rename_offenders = _scan(_package_modules(pkg), _unlocked_artifact_renames)
    ledger_seen, ledger_offenders = _scan(_package_modules(pkg), _raw_ledger_iterations)

    # Each row names the MEMBER the rule must still be able to see, not a
    # count of them: a count drifts every time a peer casting adds a read, and
    # a log body that changes for reasons unrelated to its claim cannot be
    # re-executed. "clean" and "blind" print differently here, which is the
    # whole of D-142.
    out = [
        "== the three rules over the shipped source, after this casting's fixes ==",
        "   unguarded document loads      : %s   (sees %s: %s)" % (
            decode_offenders or "none", "foundry_state.py#read_text_file",
            "foundry_state.py#read_text_file" in decode_seen),
        "   unlocked artifact renames     : %s   (sees %s: %s)" % (
            rename_offenders or "none", "foundry.py#_atomic_rename_write",
            "foundry.py#_atomic_rename_write" in rename_seen),
        "   raw ledger iterations         : %s   (sees %s: %s)" % (
            ledger_offenders or "none", "fix_gate.py#foundry_sync_defects",
            "fix_gate.py#foundry_sync_defects" in ledger_seen),
        "",
        "== and a FRESH copy planted where a directory literal could not see it ==",
        "   %-34s %s" % ("planted at", "what the derived scan reports"),
        "   %-34s %s" % ("-" * 10, "-" * 29),
    ]
    plants = [
        ("<pkg root>/regressed.py", _PLANTED_LOADER, _unguarded_document_loads),
        ("<pkg>/parsers/regressed.py", _PLANTED_LOADER, _unguarded_document_loads),
        ("<pkg root>/regressed.py", _PLANTED_WRITER, _unlocked_artifact_renames),
        ("<pkg>/schemas/regressed.py", _PLANTED_WRITER, _unlocked_artifact_renames),
        ("<pkg root>/regressed.py", _PLANTED_LEDGER_SCAN, _raw_ledger_iterations),
        # D-137 — the two spellings the OLD name-matching rule called guarded.
        ("<pkg root>/decode_only.py", _PLANTED_DECODE_ONLY_LOADER,
         _unguarded_document_loads),
        ("<pkg>/schemas/oserror_decode.py", _PLANTED_OSERROR_DECODE_LOADER,
         _unguarded_document_loads),
        # D-141/D-147 — the second shipped tree, and the three spellings the
        # string match called clean: an aliased import, a from-import, and a
        # read split from its decode across two statements.
        ("<scripts>/brand-new-cli.py", _PLANTED_OSERROR_DECODE_LOADER,
         _unguarded_document_loads),
        ("<pkg root>/aliased.py", _PLANTED_ALIASED_LOADER, _unguarded_document_loads),
        ("<pkg root>/from_import.py", _PLANTED_FROM_IMPORT_LOADER,
         _unguarded_document_loads),
        ("<pkg root>/split.py", _PLANTED_SPLIT_LOADER, _unguarded_document_loads),
    ]
    for label, source, rule in plants:
        relative = (
            label.replace("<pkg root>/", "")
            .replace("<pkg>/", "")
            .replace("<scripts>/", "scripts/")
        )
        module = _plant(tmp_path, relative, source)
        found = rule(module)[1]
        out.append("   %-34s %s" % (label, found or "MISSED"))
    return "\n".join(out)




def _external_spec_run(tmp_path: Path, spec_relative: str, spec_bytes: bytes,
                       state_key: str = "spec_path") -> tuple[str, Path]:
    """A run whose spec is DECLARED in state.json and lives outside the run dir."""
    root = tmp_path / "proj"
    fdir = root / "foundry-archive" / "d145"
    (fdir / "castings").mkdir(parents=True)
    spec = root / spec_relative
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_bytes(spec_bytes)
    (fdir / "state.json").write_text(
        json.dumps({"phase": "F0.9", "cycle": 0, state_key: spec_relative}),
        encoding="utf-8",
    )
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps({
            "castings": [{"id": 1, "title": "t", "key_files": ["a.py"]}],
            "waves": [{"wave": 1, "casting_ids": [1]}],
        }),
        encoding="utf-8",
    )
    (fdir / "castings" / "casting-1-prompt.md").write_text(
        "# casting 1\n<spec_requirements>\nAC-001\n</spec_requirements>\n",
        encoding="utf-8",
    )
    foundry_state.set_active_run("d145")
    return str(root), fdir




def test_a_corrupt_spec_outside_the_run_dir_is_refused_not_raised(tmp_path, monkeypatch):
    """D-145 on the defect path: the door that raised now refuses by name."""
    from foundry_mcp import server as srv

    root, fdir = _external_spec_run(tmp_path, "forge-specs/probe/spec.md", _BAD_UTF8_SPEC)
    try:
        monkeypatch.setattr(srv, "_project_root", root)
        # The guard SEES it now — the assertion the driving evidence inverted.
        problems = artifacts._run_artifact_problems(fdir)
        assert any("spec.md" in p for p in problems), problems

        result = srv._DISPATCH["Foundry-Validate-Castings"]({})
        assert result["passed"] is False, result
        assert "spec.md" in result["error"], result
        assert result.get("hint"), result
    finally:
        foundry_state.clear_active_run()




def test_the_same_spec_read_healthy_still_passes_the_door(tmp_path, monkeypatch):
    """The control that keeps the refusal NARROW.

    Without it the widened guard could refuse every run that declares a spec
    outside its own directory — which is every run this repo has — and the
    "fix" would be an outage.
    """
    from foundry_mcp import server as srv

    root, fdir = _external_spec_run(
        tmp_path, "forge-specs/probe/spec.md", _BAD_UTF8_SPEC.replace(b"caf\xe9", b"cafe")
    )
    try:
        monkeypatch.setattr(srv, "_project_root", root)
        assert artifacts._run_artifact_problems(fdir) == []
        result = srv._DISPATCH["Foundry-Validate-Castings"]({})
        assert "could not be read" not in str(result.get("error", "")), result
    finally:
        foundry_state.clear_active_run()




def _setup_worktree_root_dirnames() -> list[str]:
    """The directory `_setup_worktree` nests worktrees under, read from itself.

    `base = run_dir / <dirname> / f"{dir_prefix}{casting_id}"` — the string
    joined onto `run_dir` is the directory EVERY worktree this package creates
    lands in, whatever prefix sits beneath it. A list, not a string, so a
    writer that grew a second root is a visible disagreement rather than a
    silently-picked first hit.
    """
    import ast
    import inspect
    import textwrap

    from foundry_mcp.tools import worktree_helpers

    source = textwrap.dedent(inspect.getsource(worktree_helpers._setup_worktree))
    fn = ast.parse(source).body[0]
    roots: list[str] = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        left, right = node.left, node.right
        if (
            isinstance(left, ast.Name)
            and left.id == "run_dir"
            and isinstance(right, ast.Constant)
            and isinstance(right.value, str)
        ):
            roots.append(right.value)
    return roots




def test_a_declared_external_input_is_still_named_beside_a_live_sweep_worktree(
    tmp_path, monkeypatch
):
    """D-206 ADJACENT-PATH TEST.

    The defect's own path is the rglob limb of `_run_artifact_problems` reached
    through `_artifact_guard`. The ADJACENT path driven here is the OTHER limb
    of the same function — `_declared_external_inputs`, D-145's addition, which
    runs AFTER the walk and resolves files OUTSIDE the run directory entirely.
    The prune is a change to the walk, and a prune that returned early, or that
    swallowed the whole function's return, would take this limb with it: the
    run's declared spec would stop being guarded the moment any worktree
    existed, which is a silent under-report and the direction D-138 says is the
    unrecoverable one.

    Driven with the sweep worktree POPULATED, because that is the concurrent
    state the defect occurs in — the sweep is in flight while another door is
    called — and the external input must still be named through it.
    """
    from foundry_mcp import server as srv

    root, fdir = _external_spec_run(
        tmp_path, "forge-specs/probe/spec.md", _BAD_UTF8_SPEC
    )
    try:
        _populate_sweep_worktree(fdir)
        monkeypatch.setattr(srv, "_project_root", root)

        problems = artifacts._run_artifact_problems(fdir)
        assert any("spec.md" in p for p in problems), problems
        # ...and NOTHING from inside the worktree rode along with it.
        assert not any("payload.log" in p for p in problems), problems

        result = srv._DISPATCH["Foundry-Validate-Castings"]({})
        assert result["passed"] is False, result
        assert "spec.md" in result["error"], result
    finally:
        foundry_state.clear_active_run()




# --------------------------------------------------------------------------- #
# D-157 (FR-019 / AC-004 / FR-020 / AC-025 / NFR-002 / ST-003) — A REFUSAL THE
# HANDLER NAMED MUST REACH THE OPERATOR'S SCREEN.
#
# THE HARM, driven at 1e07a4c. Same fixture as D-145 above: the run's ONE
# declared external input made undecodable, the run-directory copy left healthy.
# `Foundry-Validate-Castings` declines naming both artifact and cause.
# `Foundry-Next` returns is_error=false and renders 109 characters -- the banner
# with a literal '?' for the phase and 'Action: ?' for the imperative -- naming
# no artifact, no cause, no hint. A normal-priority directive filed BEFORE the
# corruption renders on the healthy arm and is GONE on the corrupt one, with no
# statement that anything is wrong (FR-019 / AC-004).
#
# WHERE IT ACTUALLY WAS, and it is not where the report guessed. Every
# orchestrator door DOES run `_artifact_guard`, and the guard's membership DOES
# include the declared external input -- `foundry_next_action:4547` returns the
# refusal dict naming spec.md, with hint and corrupt_artifacts, on this exact
# fixture. The refusal died one hop later, in `display._fmt_foundry_next_action`,
# which reads `display` / `instructions` / `phase` / `action` and renders '?' for
# each when a refusal carries none of them.
#
# AND IT WAS NEVER ONE FORMATTER (ST-003). Driven across the whole table with
# one house refusal, FIVE of the twenty-two dropped it: Foundry-Next,
# Foundry-Context -- the ADJACENT door this defect names -- Foundry-Init,
# Validate-Report and Verify-Citations. The other seventeen each carry their own
# hand-written `if r.get("error")` branch. Whether a refusal reached the screen
# was decided once per formatter, which is the class exactly: the hardening was
# bound at each site instead of derived over every member.
#
# THE FIX IS AT THE ROUTER, as a post-condition over whatever the formatter
# produced (`display.format_result`): a result that NAMES a refusal is rendered
# CARRYING that refusal, or the router renders the house block itself. The tests
# below assert the property over `_FORMATTERS` -- present and future -- rather
# than over the five names found today.
# --------------------------------------------------------------------------- #


def _dispatched_orchestrator_doors() -> dict[str, str]:
    """``{tool name: handler function}`` for every orchestrator door on _DISPATCH.

    The same derivation `test_every_orchestrator_entry_point_runs_the_artifact_guard`
    uses, kept as one reading of `server.py` so the structural assertion and the
    behavioural drive below cannot disagree about which doors exist.
    """
    import foundry_mcp.server as foundry_server

    doors = _dispatched_functions(Path(foundry_server.__file__).resolve())
    return {
        tool: fn for fn, tool in doors.items()
        if orchestration_has(fn)
    }




def _minimal_declared_args(tool: str) -> dict:
    """The smallest argument set a tool's OWN declared schema calls required.

    Synthesized from the declaration the server publishes, never typed per
    tool: a door that grows a required field is still driven the day it grows
    one. A hand-kept argument table beside the drive is the same "remember to
    enrol it" failure every rule in this file exists to close.
    """
    import asyncio

    import foundry_mcp.server as foundry_server

    schemas = {t.name: (t.inputSchema or {}) for t in asyncio.run(foundry_server.list_tools())}
    schema = schemas[tool]
    properties = schema.get("properties", {}) or {}
    filler = {"string": "x", "integer": 1, "number": 1,
              "boolean": False, "array": [], "object": {}}
    args = {}
    for name in schema.get("required", []) or []:
        declared = properties.get(name, {}) or {}
        if declared.get("enum"):
            args[name] = declared["enum"][0]
        elif name in ("items_checked", "items_total"):
            # The stream door refuses items_checked<=0 by its own rule, and this
            # drive is about the ARTIFACT guard, not that one.
            args[name] = 1
        else:
            args[name] = filler.get(declared.get("type", "string"), "x")
    return args




def _corrupt_external_input_door_drive(tmp_path: Path, tool: str) -> tuple[dict, str]:
    """Drive one door on its OWN fresh corrupt-external-input run.

    A fresh root per door because these doors write: a door that failed to
    refuse would mutate the run the next door then reads, and the second
    failure would be blamed on the wrong door.
    """
    from foundry_mcp import server as srv

    root, _fdir = _external_spec_run(
        tmp_path / tool, "forge-specs/probe/spec.md", _BAD_UTF8_SPEC
    )
    saved = srv._project_root
    srv._project_root = root
    try:
        result = srv._DISPATCH[tool](_minimal_declared_args(tool))
    finally:
        srv._project_root = saved
        foundry_state.clear_active_run()
    return result, format_result(tool, result)




def test_every_orchestrator_door_refuses_a_corrupt_declared_external_input(tmp_path):
    """D-157's fix surface: the guard's MEMBERSHIP asserted PER DOOR.

    `test_every_orchestrator_entry_point_runs_the_artifact_guard` asserts that
    each door CALLS the guard. That is not the same claim as "each door refuses
    by name when the run's declared EXTERNAL input is the broken one" -- a guard
    whose membership had narrowed back to the run directory would satisfy the
    structural test at every door while every door went on acting on a document
    it had to guess at. So this drives the real _DISPATCH, per door, on the
    external-input fixture, and asserts the whole chain the operator depends on:
    the response names the artifact, and the RENDERING of that response names it
    too (D-157: the second half is where it was actually lost).
    """
    doors = _dispatched_orchestrator_doors()
    assert len(doors) >= 12, doors

    silent = []
    for tool in sorted(doors):
        result, rendered = _corrupt_external_input_door_drive(tmp_path, tool)
        # `error` is the house key; `reason` is the gate's variant of the same
        # refusal. Both are read, so a door is never scored blind for choosing
        # the shape its own result type already had.
        text = " ".join(str(result.get(key, "")) for key in ("error", "reason"))
        corrupt = result.get("corrupt_artifacts") or []
        if not (
            "spec.md" in text
            and any("spec.md" in str(p) for p in corrupt)
            and "spec.md" in rendered
        ):
            silent.append(
                f"{tool} -> named={'spec.md' in text} "
                f"corrupt_artifacts={corrupt} rendered_names_it={'spec.md' in rendered}"
            )

    assert silent == [], (
        f"these doors consume the run's DECLARED EXTERNAL INPUT and do not "
        f"refuse by name when it cannot be read: {silent}. Every entry point "
        f"must go through the same guard AND the refusal must survive to the "
        f"rendering, or the operator gets a banner with a '?' in it while the "
        f"run's own spec sits on disk unreadable."
    )




def _shipped_cli(name: str) -> Path:
    """One of the three CLIs, located through the SAME derivation the scan uses."""
    return _scanned_roots()[-1] / name




def _load_cli_module(name: str):
    """Import a hyphenated, non-importable CLI by path, as its own tests do."""
    import importlib.util

    path = _shipped_cli(name)
    spec = importlib.util.spec_from_file_location(f"_{name.replace('-', '_')}_ut", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module




def render_shipped_tree_decode_table(tmp_path: Path) -> str:
    """D-141 pre/post: one non-UTF-8 byte through the three shipped CLIs.

    The PRE arm reproduces the handler set all three scripts held, verbatim,
    rather than describing it — so the log shows the raise instead of
    asserting it happened once.
    """
    import subprocess

    def old_load(p: Path):
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, FileNotFoundError):
            return None

    document = tmp_path / "defects.json"
    document.write_bytes(_BAD_UTF8_DOCUMENT)

    scripts_root = _scanned_roots()[-1]
    out = [
        "== D-141: the decode rule's ROOT stopped at the installed package ==",
        "",
        "-- the two shipped source trees, both derived from foundry_mcp.__file__ --",
    ]
    # Named, not counted: a module count drifts every time a peer casting adds
    # a file, and a log whose body changes for reasons unrelated to its claim
    # is a log nobody can re-execute. What matters is that the second root is
    # REACHED and that the three CLIs D-141 names are inside it.
    for root in _scanned_roots():
        found = sorted(p.name for p in root.rglob("*.py"))
        out.append(
            f"   {root.name:24s} is_dir={root.is_dir()}  non-empty={bool(found)}"
        )
    out.append(f"   the second root holds    : {sorted(p.name for p in scripts_root.glob('*.py'))}")
    out += [
        "",
        "-- the handler set all three CLIs held, driven on one non-UTF-8 byte --",
    ]
    try:
        old_load(document)
        out.append("   the 3-script shape : returned (no raise)")
    except Exception as exc:
        out.append(f"   the 3-script shape : RAISES {type(exc).__name__}")

    out += ["", "-- the same byte through the shipped loaders, post-fix --"]
    for name in ("measure-run.py", "migrate-archive.py"):
        module = _load_cli_module(name)
        out.append(f"   {name:26s} _load_json -> {module._load_json(document)!r}")

    out += ["", "-- and through the real process boundary, each CLI's own entry --"]
    fdir = tmp_path / "foundry-archive" / "d141"
    fdir.mkdir(parents=True, exist_ok=True)
    (fdir / "defects.json").write_bytes(_BAD_UTF8_DOCUMENT)
    (fdir / "state.json").write_text(json.dumps({"cycle": 2}), encoding="utf-8")
    observation = tmp_path / "observation.json"
    observation.write_bytes(_BAD_UTF8_DOCUMENT)
    for name, args in (
        ("measure-run.py", [str(fdir)]),
        ("migrate-archive.py", [str(fdir)]),
        ("validate-test-observations.py", [str(observation)]),
    ):
        proc = subprocess.run(
            [sys.executable, str(scripts_root / name), *args],
            capture_output=True, text=True, timeout=60,
        )
        combined = proc.stdout + proc.stderr
        out.append(
            f"   {name:30s} exit={proc.returncode}  traceback="
            f"{'Traceback' in combined}  names-a-token="
            f"{'MALFORMED' in combined or 'SCHEMA_INVALID' in combined}"
        )
    return "\n".join(out)




def render_external_input_guard_table(tmp_path: Path) -> str:
    """D-145 pre/post: a corrupt spec DECLARED outside the run directory."""
    from foundry_mcp import server as srv

    def old_read(p: Path) -> str:
        return p.read_text(encoding="utf-8") if p.exists() else ""

    out = ["== D-145: the run's spec lives OUTSIDE the run directory =="]
    root, fdir = _external_spec_run(tmp_path / "pre", "forge-specs/probe/spec.md", _BAD_UTF8_SPEC)
    spec = Path(root) / "forge-specs/probe/spec.md"
    out += [
        "",
        f"   state.json declares spec_path : forge-specs/probe/spec.md",
        f"   ...which resolves OUTSIDE     : {spec.resolve().is_relative_to(fdir.resolve()) is False}",
        "",
        "-- the reader's old shape, driven on one non-UTF-8 byte --",
    ]
    try:
        old_read(spec)
        out.append("   spec_path.read_text : returned (no raise)")
    except Exception as exc:
        out.append(f"   spec_path.read_text : RAISES {type(exc).__name__}")

    out += ["", "-- what the artifact guard saw, before and after --"]
    out.append(f"   rglob over the run dir alone  : {[]}")
    out.append(
        f"   ...plus the run's declared inputs: "
        f"{[p.split(' could')[0] for p in artifacts._run_artifact_problems(fdir)]}"
    )

    out += ["", "-- and the door itself, through the real _DISPATCH --"]
    real_root = srv._project_root
    try:
        srv._project_root = root
        result = srv._DISPATCH["Foundry-Validate-Castings"]({})
        out.append(
            f"   Foundry-Validate-Castings  passed={result.get('passed')}  "
            f"names the file={'spec.md' in str(result.get('error', ''))}  "
            f"hint={bool(result.get('hint'))}"
        )
        out.append(f"      {str(result.get('error', '')).split(': /')[0]}")
    except Exception as exc:
        out.append(f"   Foundry-Validate-Castings  RAISED {type(exc).__name__} across MCP")
    finally:
        srv._project_root = real_root
        foundry_state.clear_active_run()

    out += ["", "-- control: the same run with a healthy spec is untouched --"]
    good_root, good_fdir = _external_spec_run(
        tmp_path / "post", "forge-specs/probe/spec.md",
        _BAD_UTF8_SPEC.replace(b"caf\xe9", b"cafe"),
    )
    try:
        srv._project_root = good_root
        result = srv._DISPATCH["Foundry-Validate-Castings"]({})
        out.append(f"   guard problems = {artifacts._run_artifact_problems(good_fdir)}")
        out.append(
            f"   Foundry-Validate-Castings  read-fault reported="
            f"{'could not be read' in str(result.get('error', ''))}"
        )
    finally:
        srv._project_root = real_root
        foundry_state.clear_active_run()

    out += ["", "-- and the membership is DERIVED over state.json, not over a key --"]
    new_root, new_fdir = _external_spec_run(
        tmp_path / "newkey", "inputs/context.txt", _BAD_UTF8_SPEC,
        state_key="operator_context_path",
    )
    try:
        out.append(
            f"   a key nobody has heard of -> "
            f"{[p.name for p in artifacts._declared_external_inputs(new_fdir)]} reported="
            f"{[p.split(' could')[0] for p in artifacts._run_artifact_problems(new_fdir)]}"
        )
    finally:
        foundry_state.clear_active_run()
    return "\n".join(out)




def render_load_spelling_table(tmp_path: Path) -> str:
    """D-147 pre/post: what the string match saw, and what resolution sees.

    The PRE arm is the shipped predicate reproduced verbatim — `func.value.id
    == "json"` — rather than described, so the log shows the miss instead of
    asserting it happened once.
    """
    def old_is_document_load(node: ast.AST) -> bool:
        if not isinstance(node, ast.Call):
            return False
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr in ("load", "loads")
            and isinstance(func.value, ast.Name)
            and func.value.id == "json"
        ):
            return False
        return _reads_a_file(node)

    def old_uncovered_by(handlers: list, namespace: dict) -> list[str]:
        """The pre-fix coverage check: a handler that RE-RAISES still counted."""
        caught: list[type[BaseException]] = []
        for handler in handlers:
            caught.extend(_handler_classes(handler, namespace)[0])
        return [
            raised.__name__
            for raised in _DOCUMENT_LOAD_RAISES
            if not any(issubclass(raised, cls) for cls in caught)
        ]

    def old_scan(path: Path) -> list[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        namespace = _module_namespace(path, tree)
        hits: list[str] = []

        def visit(node, fn, handlers):
            if not old_is_document_load(node):
                return
            if old_uncovered_by(list(handlers), namespace):
                hits.append(f"{path.name}::{fn}:{node.lineno}")

        _walk_guarded(tree, "<module>", (), visit)
        return sorted(set(hits))

    shapes = [
        ("json.loads(read_text)      (control)", _PLANTED_DECODE_ONLY_LOADER),
        ("import json as j           no handler", _PLANTED_ALIASED_LOADER),
        ("from json import loads     no handler", _PLANTED_FROM_IMPORT_LOADER),
        ("read and decode split      half each", _PLANTED_SPLIT_LOADER),
        ("except Exception: raise Boom()      ", _PLANTED_RERAISING_LOADER),
        ("both rungs answered        (control)", _PLANTED_COVERED_SPLIT_LOADER),
    ]
    out = [
        "== D-147: the handler axis was derived and the load axis was a string ==",
        "",
        "   %-38s %-9s %s" % ("the operation, spelled", "old rule", "derived rule"),
        "   %-38s %-9s %s" % ("-" * 22, "-" * 8, "-" * 12),
    ]
    for i, (label, source) in enumerate(shapes):
        module = _plant(tmp_path, f"spelling{i}.py", source)
        old = "REPORTED" if old_scan(module) else "clean"
        new = "REPORTED" if _unguarded_document_loads(module)[1] else "clean"
        out.append("   %-38s %-9s %s" % (label, old, new))
    out += [
        "",
        "-- the live alias this was filed against --",
        "   foundry_orchestrator.py carries `import json as _json`: %s" % (
            "import json as _json"
            in (orchestration_source())
        ),
        "",
        "-- and an unresolvable spelling ANNOUNCES itself rather than passing --",
    ]
    mystery = _plant(
        tmp_path,
        "mystery.py",
        "from some_vendor_lib import codec\n"
        "\n"
        "def _load(path):\n"
        "    return codec.loads(path.read_text(encoding='utf-8'))\n",
    )
    out.append("   %s" % _unguarded_document_loads(mystery)[1])
    return "\n".join(out)




def test_no_module_in_this_castings_files_retypes_the_requirement_families():
    """The KEY LINK, asserted where its loss would be silent.

    A module that re-types the literal keeps working and quietly disagrees with
    the vocabulary the day a family is added — which is exactly how six copies
    came to exist. Casting 3 owns the package-wide scan; this pins THIS
    casting's four sites against the locked export by name.
    """
    from foundry_mcp.schemas.vocab import REQUIREMENT_ID_RE

    # fallout FR-004: the four sites are thirteen modules plus foundry_validate
    # now. NO module may carry the literal — that half is universal and is what
    # D-150 was filed on. The second half asks only of the modules that ACTUALLY
    # read requirement ids: a module with no id grammar in it has nothing to
    # import, and demanding the import there would be demanding a dead name.
    scanned = 0
    for module in (*ORCHESTRATION, __import__(
        "foundry_mcp.tools.foundry_validate", fromlist=["x"]
    )):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "US|FR|NFR|AC|VC|IR|TR" not in source, (
            f"{Path(module.__file__).name} still carries its own copy of the "
            f"requirement-ID literal. Import REQUIREMENT_ID_RE."
        )
        if "-007" in source or "requirement_id" in source.lower():
            if "REQUIREMENT_ID_RE" in source or "is_requirement_id" in source:
                scanned += 1
    assert scanned >= 2, (
        "no module reads requirement ids through the vocabulary at all, so the "
        "second half of this rule is asserting nothing"
    )

    # ...and the export really is a superset of what the copies matched.
    for family in _OLD_ID_FAMILIES + _widened_id_families():
        assert REQUIREMENT_ID_RE.findall(f"see {family}-007 here") == [f"{family}-007"]




def test_the_report_tool_is_registered_and_dispatched(run_env):
    """CT-014's registration half: a generator nothing can call is not a tool."""
    from foundry_mcp import server as foundry_server

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}
    assert "Foundry-Report" in tools
    assert tools["Foundry-Report"].inputSchema["properties"] == {}

    project_root, fdir = run_env
    _write_spec(fdir, ["FR-1"])
    _write_state(fdir, phase="F4", cycle=1)
    _write_verdicts(fdir, [{"requirement_id": "FR-1", "verdict": "VERIFIED"}])
    _defect_ledger(fdir, [])

    previous = foundry_server._project_root
    try:
        foundry_server._project_root = project_root
        result = foundry_server._DISPATCH["Foundry-Report"]({})
    finally:
        foundry_server._project_root = previous

    assert result["ok"] is True, result
    assert (fdir / "report.json").exists()




def test_the_init_schema_advertises_max_cycles(run_env):
    """CT-016's registration half: the flag has to be reachable over MCP.

    A `max_cycles` the handler persists but the schema never advertises is a cap
    no run can set — the shape that made `casting_commit` always-None and
    `inspect_start` unreachable.

    fallout D-067 (casting 4's concern C-043) — AND THE WIRE'S DEFAULT IS THE
    HANDLER'S DEFAULT, WHICH IS NOW ABSENCE RATHER THAN ZERO.

    This asserted `default == 0` on both halves, which was the pair agreeing on
    the wrong answer: CT-006's input domain is "integer N at least 0" and 0 is
    the one member of it a resume did not write, because the handler could not
    tell an omitted flag from an explicit 0 and the schema default plus the
    dispatch `.get(..., 0)` between them guaranteed it never would. The two
    halves still have to agree — that is what this test is for — so it now
    asserts the shape they agree on: no schema default at all, and `None` on the
    signature. `type` and `minimum` stay asserted because both still do work.
    """
    from foundry_mcp import server as foundry_server

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}
    prop = tools["Foundry-Init"].inputSchema["properties"]["max_cycles"]
    assert prop["type"] == "integer"
    assert prop["minimum"] == 0
    assert "default" not in prop, (
        "the Foundry-Init schema carries a `max_cycles` default again. A "
        "default a validator or client fills in manufactures an explicit 0 out "
        "of an absent key, and an explicit 0 REWRITES a resumed run's cap to "
        "unbounded — so the default lifts the ceiling of every bare resume."
    )

    import inspect

    from foundry_mcp.tools import foundry as foundry_module

    params = inspect.signature(foundry_module.foundry_init).parameters
    assert params["max_cycles"].default is None

    # ...and the dispatch entry between them substitutes nothing either, which
    # is the third surface the same value passes through.
    source = inspect.getsource(foundry_server)
    assert 'max_cycles=args.get("max_cycles")' in source, (
        "the Foundry-Init dispatch entry no longer passes an absent "
        "`max_cycles` through as absence"
    )




def test_the_spawn_tool_descriptions_name_the_field_that_carries_the_text(
    run_env
):
    """D-012's other half: 'Both spawn tools" MCP description= strings in
    server.py#list_tools repeat it ("The lead MUST pass the returned prompt
    field").'

    The MCP description is an instruction surface the lead reads directly, so a
    stale one is not documentation drift — it is a second, contradictory order.
    """
    from foundry_mcp import server as foundry_server

    tools = {t.name: t for t in asyncio.run(foundry_server.list_tools())}

    for name in ("Foundry-Spawn-Teammate", "Foundry-Cast-Wave"):
        description = tools[name].description
        assert "`dispatch`" in description, name
        assert "MUST pass the returned `prompt` field" not in description, name
        assert "full_prompt" in description, name




# --------------------------------------------------------------------------- #
# D-081 / D-082 — HALTED IS TERMINAL, AND EVERY DOOR READS IT
#
# ST-008: 'HALTED is not DONE.' CT-016: 'a named terminal state distinct from
# DONE.' FR-024: 'the run ends in a named HALTED state rather than DONE.'
# `_halt_if_capped` was the only code in the server that mentioned the state at
# all — it WROTE `phase = HALTED` from the two doors that open a GRIND, and
# nothing anywhere read it back. So a halted run walked to F6 DONE through a
# gate that reported itself passed, and every phase token other than the two
# that re-halt resumed the run outright.
# --------------------------------------------------------------------------- #


def _gate_phase_tokens() -> set[str]:
    """Every gate token the server accepts, from the table that decides it.

    fallout AC-059 — `foundry_gate` HAS NO PER-PHASE BRANCHES ANY MORE, so
    reading its AST for `phase == "<literal>"` comparisons now finds none and
    this derivation returned the empty set — which pytest reports as "got empty
    parameter set" and every parametrized guard below silently stops covering
    anything. That is the failure mode a derived pin is most exposed to, and the
    fix is to derive from what actually decides the answer: `GATE_TO_TRANSITION`
    is the one mapping, `foundry_gate` refuses anything absent from it by name,
    and a token added there is walked by every guard below on the day it lands.
    """
    tokens = set(GATE_TO_TRANSITION)
    assert tokens, "the gate token roster is empty; every guard below is vacuous"
    return tokens




@pytest.mark.parametrize("token", sorted(_handler_phase_tokens()))
def test_no_phase_token_leaves_halted(run_env, token):
    """D-082 / ST-008 / CT-016: no transition leaves HALTED, and the refusal
    names the halt.

    Driven from state.phase HALTED with max_cycles 2, before the fix:
    `Foundry-Phase('inspect_start')` returned ok, set phase F2 and ADVANCED the
    cycle counter; `cast` returned ok and set F2; `temper` returned ok and set
    F5; `nyquist` returned ok and set F5.5. Only `grind_start` and `assay_fail`
    re-halted, because only they call `_halt_if_capped` — every other branch had
    no HALTED precondition at all. So a halted run resumed and kept dispatching
    with no refusal and no record that the cap had been overridden, and FR-052's
    'Foundry-Next reports halted and stops dispatching' rested on lead
    discipline, which is the thing the cap exists to replace.

    Parametrized over the token set the drift guard derives from
    `_phase_transition`'s OWN AST, so a branch added later is covered the day it
    is added rather than the day someone remembers to extend a list.
    """
    project_root, fdir = run_env
    _halted_run(fdir)
    _defect_ledger(fdir, [_tiered("D-001", "LIVE")])
    _arm_ordering_token(fdir)

    result = foundry_mark_phase_complete(token, project_root)

    assert result.get("ok") is not True, (token, result)
    assert result["halted"] is True
    assert "HALTED" in result["error"], result
    assert "NOT DONE" in result["error"], result
    assert "Nothing leaves HALTED" in result["hint"], result

    # The run did not move: not the phase, not the counter.
    state = json.loads((fdir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == RUN_PHASE_HALTED, token
    assert state["cycle"] == 2, token




@pytest.mark.parametrize("phase", sorted(_gate_phase_tokens()))
def test_every_gate_refuses_from_halted(run_env, phase):
    """D-081's gate half: a halted run has no next gate, so no gate reports
    itself passed.

    `done` and `nyquist_done` are what made this a defect — driven, max_cycles 2
    at cycle 2 with one open LATENT defect: `Foundry-Phase('grind_start')`
    returned ok / halted True and state.phase HALTED, and two calls later
    `Foundry-Gate('done')` returned passed True with reason None. The gate
    agreed the run could finish while state.json said it had already stopped.
    The answer is the same for every phase, which is why this is parametrized
    over the branch set rather than over the two terminal tokens.
    """
    project_root, fdir = run_env
    _halted_run(fdir)
    _defect_ledger(fdir, [])
    _arm_ordering_token(fdir)

    gate = foundry_gate(phase, project_root)

    assert gate["passed"] is False, (phase, gate)
    assert gate["halted"] is True
    assert "HALTED" in gate["reason"]
    # fallout AC-062 (D-088) — THE REFUSAL IS THE ROUTINE'S RUNG NOW, so it is
    # published as a ranked entry rather than as a bare sentence with no
    # `refusals` key, and it no longer carries this door's surface INSIDE the
    # reason: `_halted_sentences` is one spelling and the two doors add their
    # own clause, the transition through `_transition_refusal` and the gate
    # through the `phase` field the caller already reads. That reachability is
    # the whole of D-088 — the rung the derivation names is what speaks, rather
    # than a short-circuit standing above it.
    assert gate["phase"] == phase, gate
    assert [r["rank"] for r in gate["refusals"]] == [0], gate["refusals"]
    assert "HALTED" in gate["refusals"][0]["reason"], gate["refusals"]
    assert [c["check"] for c in gate["checklist"]] == [
        "run_not_halted (halted_at_cycle=2)"
    ]
    # A refused gate stamps nothing: `.gate-passed` is the marker the guidance
    # engine reads to emit the transition step, and a halted run has none.
    assert not (fdir / ".gate-passed").exists()




def test_the_phase_token_guard_did_not_change_the_derived_branch_set(run_env):
    """The HALTED guard compares no phase literal, so the drift guard that
    derives the accepted token set from `_phase_transition`'s own
    `phase == "<literal>"` comparisons still reads exactly the branches.

    fallout FR-064 / GI-034: the count is the ROSTER's length, not a number
    typed here. `halt` joined `PHASE_TOKENS` this release and a hand-typed 10
    would have had to be edited beside it — which is the second copy this pin
    exists to catch, in the pin itself.
    """
    assert _handler_phase_tokens() == set(PHASE_TOKENS)
    assert len(_handler_phase_tokens()) == len(PHASE_TOKENS)
    assert "halt" in _handler_phase_tokens()




# --------------------------------------------------------------------------- #
# D-101 — the filing doors advertise no obligation the handler does not enforce
#
# The D-089 LEAD RULING that made `file_path` / `file` REQUIRED on a LATENT
# filing is REVERSED (run state.json, spec_ambiguities entry 6). A tool
# description that demands a field the door accepts without is the same drift as
# an enum the handler rejects, and worse on a filing door: a stream reading it
# withholds a filing it should make, or hand-fabricates a location to satisfy a
# rule nothing checks. So both doors advertise the field as EXPECTED — which is
# true, and is what the F6 backlog renders — and neither calls it required.
# --------------------------------------------------------------------------- #


def _tool_schema(name: str) -> tuple[str, dict]:
    from foundry_mcp import server as foundry_server

    tool = next(t for t in asyncio.run(foundry_server.list_tools()) if t.name == name)
    return tool.description or "", tool.inputSchema




# --------------------------------------------------------------------------- #
# STRUCTURAL — the escalated class `stale-prose-survives-beside-new-prose`
#
# D-122, D-126, D-136, D-141, D-142, D-143, D-144 are one class, filed across
# four castings' files, and it has now recurred for four consecutive cycles.
# Two prior structural packets fixed the INSTANCES and the class came back,
# which is what makes an instance fix the wrong deliverable: the run kept
# retiring mechanisms — a roster, a key, a call, a read, a proxy, a caller list,
# a promise about how a run ends — and every surface that still described the
# retired one went on describing it until a prover happened to read that file.
#
# So the deliverable is a MECHANISM, and it is this: a retired mechanism may be
# NAMED, but only as HISTORY. The house style already requires it (see the
# "Every non-obvious decision carries its failure history in a comment" rule) —
# a comment explaining what a thing USED to do, citing the defect that changed
# it, is how the next author learns which invariant they are about to break.
# What that style cannot survive is the same sentence written in the PRESENT
# TENSE, which is indistinguishable from documentation until someone drives it.
#
# The rule is therefore positional, not lexical: naming a retired mechanism is
# fine when the surrounding block marks it as gone. Every historical mention in
# the tree today already does — "used to", "which is gone", "D-098 deleted",
# "no longer" — because the house style was already asking for it. Only the
# live assertions have no marker, and those are exactly the seven filings.
#
# The pin reads the SEVEN FILED SURFACES, whichever casting owns them, because
# the class is cross-casting and a pin scoped to one casting's files would have
# caught one of the seven.
# --------------------------------------------------------------------------- #


def _plugin_root() -> Path:
    """`plugins/foundry/`, from this file."""
    return Path(__file__).resolve().parents[4] / "foundry"


# D-136 — WHY EVERY MARKER IS A PHRASE AND NOT A WORD.
#
# This list carried the bare tokens `deleted` and `stopped`, and a bare word
# marks nothing: `commands/start.md`'s lead-fix rule says "at most 20
# added-plus-deleted lines", so the whole CRITICAL LEAD RULES list counted as
# history and the live sentence four lines below it — "Foundry runs until F6
# DONE or an error stops it", the exact D-136 filing — passed the scan. Driven
# while writing this pin, which is the point of writing the falsifiability
# test beside it. A marker has to be a phrase that can only be about something
# being gone.


def _markdown_units(text: str) -> list[tuple[int, str]]:
    """(1-based start line, text) for each blank-line-separated paragraph."""
    out: list[tuple[int, str]] = []
    start = 0
    buf: list[str] = []
    for i, line in enumerate(text.splitlines(), start=1):
        if line.strip():
            if not buf:
                start = i
            buf.append(line)
        elif buf:
            out.append((start, "\n".join(buf)))
            buf = []
    if buf:
        out.append((start, "\n".join(buf)))
    return out




def _python_units(text: str) -> list[tuple[int, str]]:
    """(1-based start line, text) for each PROSE unit in a Python file.

    A prose unit is a whole docstring, or a whole contiguous run of lines
    carrying `#` comments — taken as WHOLE SOURCE LINES, so an inline marker
    marks the line it sits on. Every remaining line of code is its own unit.

    WHY WHOLE LINES AND NOT THE COMMENT TEXT. Taking only the comment token
    made an inline `# D-136` cover its row without the row's CODE ever being
    scanned — the marker would have exempted the line by hiding it, which is
    the escape hatch this pin exists to close. Taking the whole line scans the
    code and the marker together, which is the rule as stated: a retired
    mechanism may be named, on a line that says it is gone.

    WHY NOT PARAGRAPHS. The house style writes the defect id on a comment
    block's HEADING line and the retired behaviour several paragraphs below it,
    inside the same docstring — so a paragraph-sized unit reports a marked,
    correctly-written failure-history comment as a violation. The unit a reader
    takes a claim and its qualification in together is the whole comment, and
    that is what this returns.

    WHY CODE LINES ARE JUDGED ALONE. A retired spelling in executable code is
    not prose ABOUT a mechanism, it is a use OF one, and the nearest comment is
    not what qualifies it.
    """
    import io
    import tokenize

    lines = text.splitlines()
    covered: set[int] = set()
    units: list[tuple[int, str]] = []

    comment_rows: list[int] = []
    prev_comment_line = -2

    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return _markdown_units(text)

    triple = ('"""', "'''")
    for tok in tokens:
        if tok.type == tokenize.COMMENT:
            row = tok.start[0]
            if row != prev_comment_line + 1 and comment_rows:
                units.append(
                    (comment_rows[0],
                     "\n".join(lines[r - 1] for r in comment_rows))
                )
                comment_rows = []
            comment_rows.append(row)
            covered.add(row)
            prev_comment_line = row
        elif tok.type == tokenize.STRING and tok.string.lstrip("rbuRBU").startswith(triple):
            for row in range(tok.start[0], tok.end[0] + 1):
                covered.add(row)
            units.append((tok.start[0], tok.string))
    if comment_rows:
        units.append(
            (comment_rows[0], "\n".join(lines[r - 1] for r in comment_rows))
        )

    for row, line in enumerate(lines, start=1):
        if row not in covered and line.strip():
            units.append((row, line))
    return units




def test_the_tripwire_caller_roster_names_the_callers_that_exist():
    """D-144 as a mechanical pin rather than a prose one.

    `record_denylist_tripwire`'s docstring carries a roster of its callers and
    its own instruction to "Re-derive this list from `grep -rn
    'record_denylist_tripwire' src/` when you change a caller; do not trust it
    because it is written down." The roster has been wrong once already — it
    named a branch D-098 deleted — which is what a hand-maintained list of
    call sites does.

    So the list is checked against the call sites, not read. Every function
    that calls the tripwire in `src/` must be named in the roster, and the
    roster names the module each lives in. `tools/foundry.py` is casting 2's
    file and this test only reads it.
    """
    import ast
    import inspect

    from foundry_mcp.tools.foundry import record_denylist_tripwire

    roster = inspect.getdoc(record_denylist_tripwire) or ""
    src_root = Path(inspect.getsourcefile(record_denylist_tripwire)).parent.parent

    callers: set[str] = set()
    for py in sorted(src_root.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "record_denylist_tripwire"
                ):
                    callers.add(node.name)

    assert callers, "the tripwire has no callers at all — the export is dead"
    missing = sorted(c for c in callers if c not in roster)
    assert not missing, (
        "record_denylist_tripwire's docstring roster does not name every "
        f"caller that exists: {missing}. Re-derive it from the call sites."
    )




# --------------------------------------------------------------------------- #
# D-186 — THE GATE LADDER: ONE REFUSAL SPEAKS, AND IT IS THE ONE TO ACT ON.
#
# `foundry_gate`'s branches were ladders of independent checks, each writing the
# function-locals `passed`, `reason` and `hint`, so the LAST failing check owned
# the two strings a terminal prints. That is a GENERATOR, not a bug in one rung:
# D-183 guarded the `.inspect-clean` rung and the rung below it —
# `no_active_teams` — did the same thing one cycle later, while assigning
# `reason` and no hint at all.
#
# The three drives are in `test_inspect_mode.py`, beside the D-183 block they
# extend. What is pinned HERE is the mechanism itself, so a rung added later
# cannot reintroduce either half: the ordering is declared rather than
# positional, no computed refusal is discarded, and no arm can claim `reason`
# without stating what clears it.
# --------------------------------------------------------------------------- #


def test_every_gate_refusal_states_a_remedy():
    """NFR-005: 'Every new refusal and notice reads correctly in an interactive
    terminal session.' A refusal with an empty `hint` states no next move.

    Derived from the module's own AST rather than from a list of arms, which is
    the whole reason it will still hold for the rung nobody has written yet:
    `_GateLadder.fail` takes `hint` as a required positional, so an arm that
    claims `reason` and states no remedy is not expressible, and this asserts
    that no call site evades it with an empty literal.
    """
    source = orchestration_source()
    # fallout FR-007 / AC-009 — THE ARMS MOVED, SO THE SCAN FOLLOWS THEM.
    #
    # `foundry_gate` composes nothing now: every `ladder.fail` in this package
    # lives in a `_<token>_preconditions` routine or in one of the rung helpers
    # they share. Scanning the gate alone would find zero arms and pass
    # vacuously — the exact failure the emptiness guard below refuses — so the
    # subject is every function that records a failing check, wherever it sits.
    bodies = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
    ]
    calls = [
        node for body in bodies for node in ast.walk(body)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fail"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "ladder"
    ]
    assert len(calls) >= 12, (
        "the package records fewer failing checks through the ladder than there "
        "are rungs; the scan has gone blind and every assertion below is vacuous"
    )
    gate = next(
        node for node in bodies if node.name == "foundry_gate"
    )
    for call in calls:
        assert len(call.args) == 3 and not call.keywords, ast.dump(call)
        rank, reason, hint = call.args
        for arg, label in ((reason, "reason"), (hint, "hint")):
            if isinstance(arg, ast.Constant):
                assert str(arg.value).strip(), f"empty {label} at line {call.lineno}"
        assert isinstance(rank, ast.Name) and rank.id.startswith("_GATE_RANK_"), (
            "a rank must be one of the named constants, so the ordering is "
            f"readable in one place (line {call.lineno})"
        )

    # ...and the three locals the last-writer-wins ladder ran on are gone, so
    # there is nothing left for a new arm to overwrite.
    stored = {
        node.id for node in ast.walk(gate)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    assert not (stored & {"passed", "reason", "hint"}), sorted(stored)




def test_no_handler_layer_function_renders_a_refusal_down_a_ladder():
    """The generator, closed by sweep rather than by one more converted
    function.

    D-186 fixed `foundry_gate`'s branches and D-183 fixed the rung above the
    one it missed; D-190 and D-191 were the same shape one function along, in
    the one place that conversion deliberately skipped. Three filings of one
    class is what a class-level guard is for, so this is PROVE's own AST sweep
    kept as a test: NO function in the handler layer may assign the rendered
    strings `reason` or `hint` in two or more SIBLING `if` statements, because
    that is exactly the shape in which the last failing check owns what a
    terminal prints.

    Siblings, specifically. An `if`/`elif`/`else` chain is ONE statement whose
    arms are mutually exclusive, so it writes the pair once and is not a
    ladder; two independent `if`s at the same level are, whatever order they
    happen to be in. `passed` is deliberately NOT swept: a boolean that only
    ever moves one way accumulates correctly from any number of arms — it is
    the STRINGS that get displaced, which is why `_GateLadder` exists.
    """
    import ast

    from foundry_mcp import server as foundry_server
    from foundry_mcp.schemas import findings, vocab
    from foundry_mcp.tools import (
        citation,
        evidence,
        foundry,
        foundry_handoff,
        foundry_report,
        foundry_spawn,
        foundry_state,
        foundry_validate,
    )

    rendered = ("reason", "hint")

    def _assigned(node: ast.AST) -> set[str]:
        """Names assigned anywhere in `node`, not descending into nested defs."""
        names: set[str] = set()
        stack = list(ast.iter_child_nodes(node))
        while stack:
            cur = stack.pop()
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.Lambda, ast.ClassDef)):
                continue
            if isinstance(cur, ast.Assign):
                names |= {t.id for t in cur.targets if isinstance(t, ast.Name)}
            elif isinstance(cur, (ast.AugAssign, ast.AnnAssign)):
                if isinstance(cur.target, ast.Name):
                    names.add(cur.target.id)
            stack.extend(ast.iter_child_nodes(cur))
        return names

    def _blocks(fn: ast.AST) -> list[list[ast.stmt]]:
        """Every statement LIST inside `fn`, not descending into nested defs."""
        out: list[list[ast.stmt]] = []
        stack = [fn]
        while stack:
            cur = stack.pop()
            for field in ("body", "orelse", "finalbody"):
                block = getattr(cur, field, None)
                if not isinstance(block, list) or not block:
                    continue
                if not all(isinstance(s, ast.stmt) for s in block):
                    continue
                out.append(block)
                stack.extend(
                    s for s in block
                    if not isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef,
                                          ast.ClassDef))
                )
            stack.extend(getattr(cur, "handlers", []) or [])
        return out

    modules = (
        *ORCHESTRATION, foundry_server, citation, evidence, foundry,
        foundry_handoff, foundry_report, foundry_spawn, foundry_state,
        foundry_validate, findings, vocab,
    )
    ladders: list[str] = []
    for module in modules:
        path = Path(module.__file__)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for block in _blocks(fn):
                siblings = [s for s in block if isinstance(s, ast.If)]
                for name in rendered:
                    writers = [s for s in siblings if name in _assigned(s)]
                    if len(writers) >= 2:
                        ladders.append(
                            f"{path.name}:{fn.name} assigns `{name}` in "
                            f"{len(writers)} sibling if-statements "
                            f"(lines {[w.lineno for w in writers]})"
                        )
    assert not ladders, (
        "a refusal ladder is back — rank the checks through `_GateLadder` "
        "instead, so the rendered pair comes from the check whose remedy no "
        "other failing check defeats: " + "; ".join(sorted(set(ladders)))
    )




def test_the_done_evaluation_ranks_every_arm_through_named_constants():
    """The mechanism half, derived from the AST like its `foundry_gate` twin.

    `test_every_gate_refusal_states_a_remedy` asserts this for `foundry_gate`;
    `_done_preconditions` is the other half of the same evaluation and had none
    of it. Every `fail` takes three positionals with a `_GATE_RANK_*` name, so
    the ordering is readable in one place, and the three locals the retired
    ladder ran on are gone so a new arm has nothing to overwrite.
    """
    import ast

    source = orchestration_source()
    fn = next(
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
        and node.name == "_done_preconditions"
    )

    calls = [
        node for node in ast.walk(fn)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "fail"
    ]
    assert len(calls) >= 8, "the done evaluation records fewer checks than it has"
    for call in calls:
        assert len(call.args) == 3 and not call.keywords, ast.dump(call)
        rank, reason, hint = call.args
        assert isinstance(rank, ast.Name) and rank.id.startswith("_GATE_RANK_"), (
            f"a rank must be one of the named constants (line {call.lineno})"
        )
        for arg, label in ((reason, "reason"), (hint, "hint")):
            if isinstance(arg, ast.Constant):
                assert str(arg.value).strip(), f"empty {label} at line {call.lineno}"

    stored = {
        node.id for node in ast.walk(fn)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    assert not (stored & {"passed", "reason", "hint"}), sorted(stored)




def test_the_two_new_tools_are_registered_the_way_the_registry_reads_them(run_env):
    """fallout GI-023 / FR-012 / FR-050 — Foundry-Concern and Foundry-Roster, on the wire.

    Casting 1 owns both handlers and tests them by direct call; what is asserted
    here is the REGISTRATION — that they are advertised, that the dispatch names
    the handler global (Holmes introspect-1: a wrapped handler, a
    functools.partial or a table lookup makes a tool vanish from the registry),
    and that a call through the transport reaches the handler.
    """
    from foundry_mcp import server as foundry_server

    names = {t.name for t in asyncio.run(foundry_server.list_tools())}
    assert {"Foundry-Concern", "Foundry-Roster"} <= names, sorted(names)
    assert {"Foundry-Concern", "Foundry-Roster"} <= set(foundry_server._DISPATCH)

    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/handler.py"], no_ui=True)
    _write_state(fdir, phase="F1", cycle=1)
    foundry_server._project_root = str(project_root)
    try:
        opened = foundry_server._DISPATCH["Foundry-Concern"]({
            "casting_id": 1, "cycle": 1, "target": "src/handler.py",
            "text": "this fix reaches a sibling casting's file",
        })
        assert opened.get("error") is None, opened
        roster = foundry_server._DISPATCH["Foundry-Roster"]({
            "stream": "prove", "items": ["FR-1", "FR-2"],
        })
        assert roster.get("error") is None, roster
    finally:
        foundry_server._project_root = "."




def test_the_concern_door_requires_a_cycle_on_the_arm_that_writes_one(run_env):
    """fallout D-060 / AC-004 / CT-001 — the registration half of the cycle rung.

    AC-004 is "with a Foundry-Concern entry from casting 2 targeting casting 5's
    file still open after GRIND, `Foundry-Phase('inspect_start')` refuses naming
    the concern id". `_inspect_start_preconditions` scopes that refusal to
    `current_cycle(fdir)`, because the requirement says "from the closing
    GRIND" — so a concern stored under a cycle nobody is in is a concern the
    gate that exists to refuse over it walks straight past.

    Three surfaces produced that stored zero and two of them are in this file:
    an inputSchema with no `required` list at all — the only ledger-WRITING door
    here without one — and a dispatch entry reading `args.get("cycle", 0)`,
    which manufactures a real cycle out of an omitted key. 0 is a cycle a
    CAST-time concern legitimately carries, so nothing downstream can tell the
    substitute from the value.

    Driven at the transport, not at the handler, because the schema is the layer
    that answers first and a direct call walks past it.
    """
    from foundry_mcp import server as foundry_server

    schema = next(
        t.inputSchema for t in asyncio.run(foundry_server.list_tools())
        if t.name == "Foundry-Concern"
    )

    # The WRITE arm without a cycle is refused, and the refusal names the field.
    refusal = foundry_server._argument_refusal("Foundry-Concern", schema, {
        "casting_id": 2, "target": "src/handler.py", "text": "reaches casting 5",
    })
    assert refusal is not None, "an omitted cycle was accepted at the door"
    assert "cycle" in refusal["missing_fields"], refusal

    # The same call carrying it passes the door...
    assert foundry_server._argument_refusal("Foundry-Concern", schema, {
        "casting_id": 2, "cycle": 4, "target": "src/handler.py",
        "text": "reaches casting 5",
    }) is None

    # ...and cycle 0 is a VALUE, not the absence the substitute used to forge.
    assert foundry_server._argument_refusal("Foundry-Concern", schema, {
        "casting_id": 2, "cycle": 0, "target": "src/handler.py",
        "text": "reaches casting 5",
    }) is None

    # The CLOSE arm neither takes a cycle nor is asked for one: it moves a
    # record that already carries its stamp (ST-004). A flat `required` here
    # would refuse this call and break the half of AC-004 that says the gate
    # passes once the lead closes the concern with a reason.
    assert foundry_server._argument_refusal("Foundry-Concern", schema, {
        "close": "C-001", "reason": "dispatched to casting 5 this cycle",
    }) is None

    # And the dispatch entry between the schema and the handler substitutes
    # nothing, so an absent key reaches the handler's own sentinel.
    import inspect

    source = inspect.getsource(foundry_server)
    assert 'cycle=args.get("cycle")' in source, (
        "the Foundry-Concern dispatch entry no longer passes an absent cycle "
        "through as absence"
    )




# --------------------------------------------------------------------------- #
# fallout AC-011 / OT-011 / GI-024 — commit group (4): ONE DEFINITION, PACKAGE-WIDE.
#
# Commit group (0) removed the last known duplicates — the transient second copy
# of Block B that `tools/artifacts.py` created, and the reader copies casting 10
# consolidated into `tools/foundry_state.py`. This passes the moment it is
# written and fails the moment anyone forks a helper again, which is the only
# kind of guard worth having against a class this package has paid for five
# times: `_load_json`, `_now`, `_current_cycle`, the spend bucket and the
# unreported-dispatch rule were each defined twice, and every pair drifted.
#
# It moves into `tests/orchestration/test_module_boundaries.py` at the carve,
# where it becomes one of that guard's four assertions.
# --------------------------------------------------------------------------- #


#: A name defined in two modules ON PURPOSE, with the reason. Not "we are fine
#: with this": every entry names why the two definitions cannot be one, and an
#: entry whose duplication has since been removed FAILS — an allowlist that
#: outlives the thing it excuses is how the exception becomes the rule.
_DELIBERATE_REDEFINITIONS: dict[str, str] = {
    "_INSPECT_PHASES": (
        "fallout GI-033 / AC-061 (D-021 / D-035) — ONE VALUE, TWO LAYERS, AND "
        "NO LEAF THAT MAY HOLD IT. The phases that ARE an INSPECT are needed by "
        "`gates._streams_complete` (verifier) and `streams._check_streams_complete` "
        "(lifecycle), which compose the same `foundry_state.check_streams_complete` "
        "call for their own layer; neither may import the other, and a run "
        "phase is not a concern `schemas/vocab.py` currently carries. So the "
        "two are pinned by BEHAVIOUR instead of by import: "
        "`test_both_streams_complete_compositions_answer_the_same_thing` drives "
        "both compositions over one run directory and asserts they agree, which "
        "is what a drift would actually break. If a phase vocabulary lands in "
        "vocab.py, both read it and this row goes."
    ),
    "_now": (
        "a per-module private timestamp helper in the leaf modules that may not "
        "import each other. `foundry_state.now_iso` is the ONE implementation; "
        "these are call-through bindings and `test_the_timestamp_has_one_"
        "implementation_however_it_is_spelled` pins that they resolve to it."
    ),
    "_agent_id_for_casting": (
        "tools/foundry_spawn.py holds the ONE implementation, because it is the "
        "door that seeds the progress ledger with the id. "
        "tools/foundry_report.py's is a lazy CALL-THROUGH — a function-local "
        "import that runs at call time, so it neither forks the derivation nor "
        "closes the cycle a module-top import would (D-013). "
        "`test_the_agent_id_has_one_implementation_however_it_is_spelled` pins "
        "that it still resolves to the one implementation."
    ),
    "main": (
        "server.py's stdio entry point and scripts/validate_intent_coverage.py's "
        "CLI. Two programs, two entry points, one conventional name."
    ),
}



#: fallout AC-011 / OT-011 — DUPLICATION THIS GUARD FOUND AND HAS NOT CLOSED.
#:
#: NOT an exemption list. Every entry is a real second definition of one rule,
#: named here with the casting that owns the file it would take to close, so the
#: guard can start being useful on the day it is written rather than on the day
#: the whole package is clean. It behaves like the allowlist above in one
#: direction and the opposite in the other: a NEW fork fails immediately, and an
#: entry whose duplication has been removed ALSO fails, so this shrinks and
#: never grows quietly.
#:
#: EVERY ROW NAMES THE CASTING THAT MUST CLOSE IT, and every one of them is a
#: file this casting may not edit. Casting 2's own share is closed: the ANSI
#: palette (imported from display.py), Block B's primitives (imported from
#: artifacts.py), and `_agent_id_for_casting`, which was never a fork at all
#: and has moved to `_DELIBERATE_REDEFINITIONS` with the pin that says so.
#: The open rows carry a `Foundry-Concern` naming their owner; see
#: foundry-archive/foundry-run-fallout/concerns.md.
_KNOWN_DUPLICATION: dict[str, str] = {
    # fallout D-061 (casting 4's concern C-042) — THE `_artifact_guard` ROW IS
    # GONE BECAUSE THE DUPLICATION IS, AND IT WENT THE WAY THE ROW PRESCRIBED.
    #
    # The row read "Closing this means RENAMING one of the two, not importing
    # one from the other; casting 4 owns that rename." Casting 4 took that
    # decision at 9892cd1: `tools/foundry.py#_artifact_guard(fdir, *names)` is
    # now `_named_artifact_guard`, and `tools/artifacts.py#_artifact_guard(fdir)`
    # is the package's only definition of the name. Both contracts survive —
    # whole-run scan versus named subset with the D-096 ledger-container rung —
    # which is why the exit was a rename rather than a deletion. The guard below
    # now asserts the single definition instead of accounting for two, and its
    # own stale-row assertion is what forced this line to be deleted rather than
    # left to rot: an inventory that never shrinks is a catalogue.
    "_REQUIREMENT_ID_RE": (
        "castings 5 and 6 — schemas/vocab.py declares the grammar; "
        "tools/evidence.py and tools/test_deriver.py each bind their own alias "
        "to it rather than importing the declaration"
    ),
    "_normalise_path": (
        "casting 1 — tools/concerns.py and schemas/vocab.py spell one rule twice"
    ),
    # fallout AC-015 / OT-011 (D-167) — MOVED FROM `_DELIBERATE_REDEFINITIONS`,
    # BECAUSE THE ROW'S OWN STATED REASON DENIED THE DUPLICATION IT EXCUSED.
    #
    # It read: "tools/concerns.py and tools/rosters.py each shape their own
    # refusal token set; the SHAPE is the house one and the token vocabularies
    # are disjoint, so there is nothing for one definition to say for both."
    # DRIVEN at HEAD 13164ab by parsing all three shipped definitions and
    # comparing the unparsed bodies: `rosters._named_refusal` and
    # `concerns._named_refusal` are the same function TO THE CHARACTER —
    # `def _named_refusal(error: str, hint: str, phase: str) -> dict:` returning
    # `{"error": error, "hint": hint, "phase": phase}`, docstring included — a
    # single statement holding no token, no vocabulary and no per-module
    # decision. The disjoint token sets are what the CALLERS pass in as `phase`
    # (ROSTER_EXISTS, CONCERN_TARGET_UNRESOLVED and the rest), not anything
    # either definition holds. So "nothing for one definition to say for both"
    # is exactly backwards: one definition says the whole of what both say.
    #
    # `tools/display.py`'s third fork genuinely IS a different function —
    # `(result: object) -> str | None`, reading `_REFUSAL_TEXT_KEYS` off a dict
    # a handler already named — and that half of the old row was sound. It is
    # not what put the row in the wrong table.
    #
    # THE COMPARISON WITH THE ROW IT SAT BESIDE IS THE MEASURE. `_now` also has
    # three definitions and also has a `_DELIBERATE_REDEFINITIONS` row, but that
    # row is BACKED by `test_the_timestamp_has_one_implementation_however_it_is_spelled`
    # and all three `_now` bodies are call-throughs to `foundry_state.now_iso` —
    # one implementation, three bindings. `_named_refusal` has no such pin and
    # is not one implementation; it is two.
    #
    # WHY THE GUARD DID NOT CATCH IT: the "a row is excusable only while it is
    # somebody else's to close" predicate tests membership of `orchestration/`,
    # `server.py` and `foundry_spawn.py` only, and `tools/concerns.py` and
    # `tools/rosters.py` are casting 1's brand-new files created BY this run —
    # so this effort's own duplication was sitting in the table meant for other
    # castings' debt. Moving it here is what makes it visible as debt, and the
    # stale-row assertion below is what will delete this line the day casting 1
    # closes it.
    "_named_refusal": (
        "casting 1 — tools/concerns.py and tools/rosters.py hold BYTE-IDENTICAL "
        "definitions of the house refusal shaper, docstring included; one "
        "definition says the whole of what both say and the disjoint token sets "
        "are what the callers pass IN. tools/display.py's third definition is a "
        "genuinely different function (it READS a refusal a handler already "
        "named) and is not part of this duplication. See concern C-083."
    ),
}




def _package_source_modules() -> list[Path]:
    """Every shipped `.py` under the installed package, tests excluded."""
    pkg = Path(foundry_mcp.__file__).resolve().parent
    return sorted(p for p in pkg.rglob("*.py") if "__pycache__" not in p.parts)


#: fallout AC-011 / OT-011 / GI-024 (D-131 / D-132) — THE SCRIPTS GI-024 NAMES,
#: WHICH THE INSTALLED PACKAGE DOES NOT CONTAIN.
#:
#: GI-024's applies-to column is `tools/foundry_state.py`, `display.py`,
#: `foundry_report.py` AND `scripts/measure-run.py`, and the spec's Data Model
#: row names the script as a consumer of these readers. AC-011 asks that "AN AST
#: TEST REFUSES A SECOND DEFINITION of any of the named helpers"; the test's
#: module set was `Path(foundry_mcp.__file__).parent.rglob("*.py")` — the
#: installed package — so the one file the invariant names by hand was never
#: parsed. DRIVEN: two top-level defs (`spend_rollup`, `markdown_sections`)
#: appended to `scripts/measure-run.py`, both shadowing the module-scope imports
#: it already makes; `test_no_top_level_symbol_is_defined_in_two_shipped_modules`
#: plus `test_measure_run.py` ran 82 passed 1 skipped, GREEN. The one
#: implementation existed, so the RULE held — the guard that is supposed to keep
#: it holding could not see the file.
#:
#: SCOPED TO WHAT GI-024 NAMES, not to `scripts/` at large. The other three
#: scripts are standalone programs that cannot import the package they operate
#: on — `migrate-archive.py` re-spells `_load_json` and `_save_json` for exactly
#: that reason — so sweeping them into the package's symbol space would import
#: twelve collisions that are not this invariant's subject and would need an
#: allowlist to carry. This adds the ONE file the requirement names.
_CONSOLIDATION_SCRIPTS = ("measure-run.py",)


#: fallout AC-011 / OT-011 / GI-024 (D-178) — GI-024's INVENTORY, ONE NAME AT A
#: TIME, WITH AC-011's FIVE ALL PRESENT.
#:
#: AC-011 names five consolidated derivations — "spend aggregation, inspect-mode
#: rows, unreported dispatches, escalated classes and markdown-section
#: splitting" — and `inspect_mode_rows`, the inspect-mode member, was the one
#: nobody typed here. The rule held (it is defined once, in
#: `tools/foundry_state.py`), so nothing was red; the guard whose job is to KEEP
#: it holding simply had no row for it, which is exactly the failure the comment
#: above `_CONSOLIDATION_SCRIPTS` says a scan over clean source cannot
#: distinguish from a working one.
#:
#: Typed rather than derived, because the message is the point: a future author
#: who re-inlines `now_iso` fails here with the helper's name in the assertion.
#: The derivation that keeps the typing honest is one test below.
_CONSOLIDATED_HELPERS = (
    "now_iso", "current_cycle", "prove_is_clean", "overlay_unreported",
    "spend_bucket", "markdown_sections", "inspect_mode_rows",
    "unreported_dispatch_pairs", "unreported_dispatch_summary",
    "escalated_class_rows", "spend_rollup",
    "_save_json", "_document_transaction", "_resolve_spec_path",
    "_declared_external_inputs", "_run_artifact_problems",
)


#: The leaf whose consolidated readers the scan below is about, spelled ONCE so
#: the three import spellings are all derived from one string.
_CONSOLIDATION_LEAF = "foundry_mcp.tools.foundry_state"


def _leaf_symbols_the_consolidation_scripts_import() -> tuple[set[str], set[str]]:
    """(bare names bound FROM the leaf, local names bound to the leaf MODULE).

    fallout AC-011 / GI-024 (D-178) — THE DERIVATION THAT KEEPS THE TYPED
    INVENTORY HONEST.

    A leaf symbol one of these scripts imports at module scope is a consolidated
    reader by construction: that import is what makes the script a CONSUMER of
    the one implementation, and a top-level `def` of the same name in the script
    shadows it — which is D-131's exact shape and the shape `inspect_mode_rows`
    was one forgotten row away from repeating.

    Module-scope only. A function-local import is not the shadowing hazard: the
    name is bound inside the call, where a module-level `def` cannot reach it.

    fallout AC-015 / OT-015 (D-081's class, seventh sweep) — ONE SPELLING BINDS
    A BARE NAME, AND THE OTHER TWO ARE READ IN ORDER TO SAY SO.

    Only `from foundry_mcp.tools.foundry_state import now_iso` puts a bare
    `now_iso` in the script's module namespace, so only that spelling can be
    shadowed by a top-level `def` and only that spelling belongs in the first
    return value. `from foundry_mcp.tools import foundry_state` and
    `import foundry_mcp.tools.foundry_state` bind the MODULE, and
    `foundry_state.now_iso` is an attribute access no `def` in the script
    reaches.

    That was already true when this read one spelling and stopped, and it is
    why nothing here was ever WRONG — but a reader could not tell the deliberate
    narrowness from the omission that produced D-192, C-115 and C-116, and no
    reading of this scope's syntax could either, which is the measurement the
    retired recogniser was retired on. So the other two spellings are
    resolved through `_submodules_named_by` — the on-disk reading both layering
    walks share — and RETURNED, where the anchor test drives them: a
    spelling-two plant must land in the second value and contribute nothing to
    the first. A narrowness that is asserted is a different object from one that
    is merely typed.
    """
    package, _, stem = _CONSOLIDATION_LEAF.rpartition(".")
    names: set[str] = set()
    module_bound: set[str] = set()
    for script in _consolidation_scan_modules():
        if script.name not in _CONSOLIDATION_SCRIPTS:
            continue
        tree = ast.parse(script.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                # A relative import reports `node.module` as a suffix rather
                # than a package path; these scripts write none, and it is left
                # to the same reading the shared walks leave it to.
                if node.level or not node.module:
                    continue
                if node.module == _CONSOLIDATION_LEAF:
                    names |= {alias.name for alias in node.names}
                    continue
                if node.module == package:
                    module_bound |= {
                        alias.asname or alias.name
                        for alias in node.names
                        if alias.name == stem
                        and _submodules_named_by(node.module, [alias.name])
                    }
            elif isinstance(node, ast.Import):
                module_bound |= {
                    # Unaliased, `import a.b.c` binds `a`; aliased, it binds the
                    # alias. Either way the leaf is reached by attribute access.
                    alias.asname or alias.name.split(".")[0]
                    for alias in node.names
                    if alias.name == _CONSOLIDATION_LEAF
                }
    return names, module_bound


def _consolidation_scan_modules() -> list[Path]:
    """The package, plus the plugin scripts GI-024's applies-to column names.

    The window the CONSOLIDATED-HELPER rule is stated over.
    `_package_source_modules` stays the window for the package's own symbol
    space, and the two are different questions: "does this package define one
    rule twice" and "does anything the plugin ships re-define a helper GI-024
    consolidated". Asking the second over the first's file set is how the guard
    came to judge a set that excluded the file the requirement names.
    """
    plugin_root = Path(artifacts.__file__).resolve().parents[4]
    assert plugin_root.name == "foundry", plugin_root
    scripts = [plugin_root / "scripts" / name for name in _CONSOLIDATION_SCRIPTS]
    for script in scripts:
        assert script.exists(), (
            f"{script} is named by GI-024's applies-to column and is not in the "
            "tree; the scan window has gone blind rather than clean"
        )
    return _package_source_modules() + scripts




def _top_level_definitions(path: Path) -> set[str]:
    """Names this module DEFINES at top level — `def`, `class`, assignment.

    An IMPORT is not a definition: a module that imports a name is reaching the
    one definition, which is the outcome this guard exists to produce rather
    than to forbid.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names




def test_no_top_level_symbol_is_defined_in_two_shipped_modules():
    """fallout AC-011 / OT-011 — the package-wide single-definition guard.

    Two definitions of one rule is not a style question: `_load_json` had two
    and they were byte-identical until they were not, `_now` had two at
    different precisions, the spend bucket had three and two of them read
    different documents. Every one of those drifted, and every one was found by
    a defect rather than by a guard.
    """
    modules = _package_source_modules()
    assert len(modules) >= 15, [str(m) for m in modules]

    defined: dict[str, list[str]] = {}
    for module in modules:
        for name in _top_level_definitions(module):
            defined.setdefault(name, []).append(module.name)

    accounted = set(_DELIBERATE_REDEFINITIONS) | set(_KNOWN_DUPLICATION)
    duplicated = {
        name: sorted(where) for name, where in defined.items()
        if len(where) > 1 and name not in accounted
    }
    assert duplicated == {}, (
        "top-level name(s) defined in more than one shipped module. Two "
        "definitions of one rule drift; delete one and import the other, or — "
        "if the two genuinely cannot be one — record the reason in "
        f"_DELIBERATE_REDEFINITIONS: {duplicated}"
    )

    # fallout AC-015 / GI-025 (D-083) — A ROW IS EXCUSABLE ONLY WHILE IT IS
    # SOMEBODY ELSE'S TO CLOSE.
    # ----------------------------------------------------------------------
    # The table's own comment says "every one of them is a file this casting may
    # not edit", and that was prose. It is the predicate now: a row survives
    # only while EVERY module defining the name is outside this casting's
    # key_files. The day a forked symbol moves into `tools/orchestration/`,
    # `tests/orchestration/`, `server.py` or `foundry_spawn.py`, the excuse
    # stops being true and the row fails — which is the difference between an
    # inventory of other people's debt and an allowlist for one's own.
    #
    # AC-015 states a property of the GUARD ("each symbol defined once"), not of
    # the survey's inventory, so a row is a deferral and has to keep earning it.
    # D-061 closed the third row this way rather than by argument: casting 4
    # renamed its `_artifact_guard` and the stale assertion below deleted the
    # row for us.
    ours = ("orchestration/", "server.py", "foundry_spawn.py")
    module_paths = {m.name: m for m in modules}
    not_ours = []
    for name in sorted(_KNOWN_DUPLICATION):
        homes = defined.get(name, [])
        for home in homes:
            path = module_paths.get(home)
            relative = "" if path is None else str(
                path.relative_to(Path(foundry_mcp.__file__).resolve().parent)
            )
            if any(part in relative for part in ours):
                not_ours.append(f"{name} in {relative}")
    assert not_ours == [], (
        f"_KNOWN_DUPLICATION row(s) whose fork now lives in THIS casting's own "
        f"files: {not_ours}. The table records duplication another casting must "
        "close, and a row over a file this casting can edit is an allowlist for "
        "its own debt. Close it here."
    )

    # ...and neither table outlives what it accounts for. This is what makes the
    # inventory shrink: closing a duplication and leaving its row here fails.
    stale = sorted(name for name in accounted if len(defined.get(name, [])) < 2)
    assert stale == [], (
        f"entr(y/ies) accounting for nothing: {stale}. The duplication is gone; "
        "take the row with it."
    )




def test_a_deliberate_redefinition_row_is_not_two_copies_of_one_body():
    """fallout AC-015 / OT-011 (D-167) — the two tables mean different things,
    and a row in the wrong one excuses a live violation.

    `_DELIBERATE_REDEFINITIONS` means "these two CANNOT be one".
    `_KNOWN_DUPLICATION` means "these two SHOULD be one and somebody else owns
    the file". A duplication in the first table is not deferred debt — it is
    debt declared impossible, and nothing will ever come back for it.

    `_named_refusal` sat in the first with a reason its own subject denied: the
    row said "the token vocabularies are disjoint, so there is nothing for one
    definition to say for both", while `concerns._named_refusal` and
    `rosters._named_refusal` are the same function TO THE CHARACTER, docstring
    included, and the token sets are what the CALLERS pass in as `phase`.

    So the predicate is structural rather than a re-reading of the prose: a
    `_DELIBERATE_REDEFINITIONS` row whose bodies are byte-identical is a row in
    the wrong table, whatever it says about itself. `_now` — the row that sat
    directly above it — passes because its three bodies are DIFFERENT: three
    call-throughs to `foundry_state.now_iso`, which is one implementation and
    three bindings, and which its own named pin asserts.
    """
    modules = _package_source_modules()
    bodies: dict[str, list[tuple[str, str]]] = {}
    for module in modules:
        source = module.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover - a shipped module always parses
            continue
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name not in _DELIBERATE_REDEFINITIONS:
                continue
            segment = ast.get_source_segment(source, node) or ""
            bodies.setdefault(node.name, []).append((module.name, segment.strip()))

    identical: dict[str, list[str]] = {}
    for name, found in bodies.items():
        seen: dict[str, list[str]] = {}
        for home, body in found:
            seen.setdefault(body, []).append(home)
        for homes in seen.values():
            if len(homes) > 1:
                identical.setdefault(name, []).extend(sorted(homes))

    assert identical == {}, (
        f"_DELIBERATE_REDEFINITIONS row(s) whose bodies are BYTE-IDENTICAL: "
        f"{identical}. That table means the two definitions cannot be one, and "
        "two identical bodies are the proof that they can — one of them says "
        "the whole of what both say. Move the row to _KNOWN_DUPLICATION naming "
        "the casting that owns the file, or close the duplication."
    )

    # The derivation's own input exists, so the assertion cannot go quietly
    # vacuous if a row is renamed out from under the scan. `_now` is the anchor
    # because it is the row this predicate must NOT fire on — three bodies, all
    # different, all call-throughs to one implementation — so a scan that found
    # nothing would be reporting the wrong kind of green.
    assert "_now" in bodies, sorted(bodies)
    assert len(bodies["_now"]) >= 2, bodies["_now"]
    assert set(bodies) <= set(_DELIBERATE_REDEFINITIONS), sorted(bodies)


def test_the_named_refusal_forks_are_the_two_the_row_names_and_not_the_third():
    """fallout AC-015 (D-167) — display.py's is a different function, and the
    row must not sweep it in.

    Half of the old row was sound: `tools/display.py#_named_refusal` is
    `(result: object) -> str | None` and READS the refusal text a handler
    already named, so it shares the noun with the other two and nothing else.
    A `_KNOWN_DUPLICATION` row that implied all three were one fork would be
    asking casting 1 to consolidate a function it does not own and that does not
    do the same thing.
    """
    signatures: dict[str, tuple[list[str], int]] = {}
    for module in _package_source_modules():
        source = module.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:  # pragma: no cover
            continue
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_named_refusal":
                args = [a.arg for a in node.args.args]
                signatures[module.name] = (args, len(node.body))

    assert set(signatures) == {"concerns.py", "rosters.py", "display.py"}, signatures
    # The two the row names take the SAME three arguments...
    assert signatures["concerns.py"][0] == ["error", "hint", "phase"]
    assert signatures["rosters.py"][0] == ["error", "hint", "phase"]
    assert signatures["concerns.py"] == signatures["rosters.py"], signatures
    # ...and the third takes one argument of a different kind entirely.
    assert signatures["display.py"][0] == ["result"], signatures["display.py"]


def test_both_streams_complete_compositions_answer_the_same_thing(run_env):
    """fallout GI-033 / AC-061 (D-021 / D-035) — the pin `_DELIBERATE_REDEFINITIONS`
    promises for `_INSPECT_PHASES`.

    `foundry_state.check_streams_complete` is the one implementation, and each
    layer composes it for itself because neither may import the other:
    `gates._streams_complete` for the verifier, `streams._check_streams_complete`
    for lifecycle. Two compositions is the cost of the layering, and the risk it
    buys is that they drift — a different INSPECT-phase tuple, a different
    fallback roster, a different marker function — and answer differently about
    the same run.

    SO THE PIN IS ON THE ANSWER, NOT ON THE LITERALS. Both are driven over ONE
    run directory in the two shapes that exercise the two constants: a run
    inside an INSPECT with a recorded roster, and a run outside one with no
    recorded width at all, which is where the fallback roster is built. A
    comparison of the two tuples would pass while the compositions passed them
    to different arguments; this fails on what a reader would actually see.

    It is NOT tautological: the two functions are separate bodies with separate
    literals, so nothing here compares an expression to itself.
    """
    project_root, fdir = run_env
    _write_manifest_with_castings(fdir, ["src/a.py"], no_ui=True)

    # (1) Outside an INSPECT, with no recorded width: the fallback roster arm.
    _write_state(fdir, phase="F1")
    assert _gates._streams_complete(project_root) == _streams._check_streams_complete(
        project_root
    )

    # (2) Inside an INSPECT with a recorded roster: the recorded-roster arm.
    _write_state(fdir, phase="F2", inspect_modes=[{
        "cycle": 0, "mode": "DELTA", "rule": "", "decided_by": "inspect_start",
        "required_streams": ["trace", "prove", "test"], "stream_scope": {},
    }])
    verifier = _gates._streams_complete(project_root)
    lifecycle = _streams._check_streams_complete(project_root)
    assert verifier == lifecycle, (verifier, lifecycle)
    # ...and the drive reached the arm it was aimed at, rather than agreeing
    # because both returned the same empty answer.
    assert verifier["required"] == ["trace", "prove", "test"], verifier




def test_the_helpers_group_zero_consolidated_have_exactly_one_definition():
    """The named half of the sweep above, so a REGRESSION names the helper.

    GI-024's inventory, one name at a time: a future author who re-inlines
    `_now` in a module that used to have its own copy fails here with the
    helper's name in the message, rather than in a dict of forty entries.
    """
    # fallout AC-011 / OT-011 / GI-024 (D-131 / D-132) — over the window the
    # RULE is stated across, which includes `scripts/measure-run.py`.
    modules = _consolidation_scan_modules()
    for helper in _CONSOLIDATED_HELPERS:
        where = [m.name for m in modules if helper in _top_level_definitions(m)]
        assert len(where) == 1, (helper, where)

    # fallout AC-011 / OT-011 / GI-024 (D-178) — AND THE TYPED INVENTORY IS NOT
    # ALLOWED TO FALL BEHIND THE SCRIPT.
    # ----------------------------------------------------------------------
    # The list above is GI-024's inventory said one name at a time, which is
    # what makes a regression name the helper instead of appearing in a dict of
    # forty. What a typed list cannot do is notice a member nobody typed:
    # `inspect_mode_rows` — the INSPECT-MODE member of AC-011's own five, and a
    # symbol `scripts/measure-run.py` imports at module scope — was simply
    # absent, and the wider sweep could not cover for it because
    # `test_no_top_level_symbol_is_defined_in_two_shipped_modules` walks
    # `_package_source_modules()` (the installed package) rather than this
    # window (the package PLUS the script GI-024 names). DRIVEN: a copy of
    # `measure-run.py` with an appended top-level `def inspect_mode_rows` —
    # shadowing the module-scope import it already makes, which is D-131's exact
    # shape — was invisible to the named pin (not in its list) and invisible to
    # the package sweep (file outside its window). Both guards stayed green.
    #
    # So the script's OWN import list is the derivation that keeps the typed one
    # honest: a leaf symbol the script imports at module scope is a consolidated
    # reader by construction — that import is what makes the script a consumer —
    # and each is held to one definition whether or not anyone remembered to
    # type it. The next reader `measure-run.py` reaches for is checked the day
    # it lands.
    imported, _module_bound = _leaf_symbols_the_consolidation_scripts_import()
    assert len(imported) >= 15, sorted(imported)
    for helper in sorted(imported):
        where = [m.name for m in modules if helper in _top_level_definitions(m)]
        assert len(where) == 1, (helper, where)
    # ...and the one AC-011 names by concern is in the TYPED list too, so a
    # regression on it fails with its own name rather than inside the sweep.
    assert "inspect_mode_rows" in _CONSOLIDATED_HELPERS
    assert imported & set(_CONSOLIDATED_HELPERS), sorted(imported)




def test_the_timestamp_has_one_implementation_however_it_is_spelled():
    """The `_now` allowlist entry, held to what it claims.

    Every module that binds the name resolves to `foundry_state.now_iso` — the
    entry says "call-through bindings", and a binding that stopped being one
    would be a second implementation wearing an exemption.
    """
    import importlib

    from foundry_mcp.tools import foundry_state

    for module_name in ("foundry_mcp.tools.orchestration.transitions",
                        "foundry_mcp.tools.concerns",
                        "foundry_mcp.tools.rosters"):
        module = importlib.import_module(module_name)
        own = getattr(module, "_now", None)
        if own is None:
            continue
        assert own() [:4].isdigit(), (module_name, own())
        # Same instant, same precision, same timezone spelling — which is the
        # thing that actually drifted when there were two.
        assert own().endswith("+00:00"), (module_name, own())
        assert len(own()) == len(foundry_state.now_iso()), module_name




def test_the_agent_id_has_one_implementation_however_it_is_spelled():
    """fallout FR-008 / GI-024 / AC-011 — the `_agent_id_for_casting` entry,
    held to what it claims.

    It sat in `_KNOWN_DUPLICATION` as "foundry_spawn.py owns it,
    foundry_report.py re-derives it", which is a description of the state D-013
    fixed rather than of the state that shipped: the second spelling is a
    function-local import that runs at call time and returns the first one's
    answer. Recording a delegation as a fork makes the inventory a list of
    things that are not wrong, which is how an inventory stops being read.

    What has to stay true is that it IS a delegation, and that is what this
    drives: the two names must agree on the id for every shape of casting id
    the spawn door mints one from — and the ids are what `Foundry-Spend` and
    `Foundry-Liveness` join a run's rows on, so the two disagreeing is the
    D-013 harm rather than an untidiness.
    """
    from foundry_mcp.tools import foundry_report, foundry_spawn

    for casting_id in (1, 12, "3", "casting-with-a-name"):
        assert (
            foundry_report._agent_id_for_casting(casting_id)
            == foundry_spawn._agent_id_for_casting(casting_id)
        ), casting_id
    # ...and the delegation is a call-through, not a re-typed f-string: the
    # report's source names the spawn door rather than the format.
    assert (
        "from foundry_mcp.tools.foundry_spawn import _agent_id_for_casting"
        in inspect.getsource(foundry_report._agent_id_for_casting)
    ), inspect.getsource(foundry_report._agent_id_for_casting)


# --------------------------------------------------------------------------- #
# fallout FR-006 / GI-025 / GI-033 / AC-015 / AC-061 / OT-015 / NFR-009 —
# THE BOUNDARY GUARD.
#
# Stdlib and pytest only. `import-linter` exists and does most of this; NFR-009
# forbids it, and the reason is not squeamishness about dependencies: a rule the
# suite can express in forty lines of `ast` is a rule a reader can check against
# the code, and a rule in a third-party config file is one nobody reads until it
# fires. Four assertions, each with its own emptiness guard:
#
#   1. the import graph among shipped modules is ACYCLIC at module top;
#   2. no top-level symbol is defined in two of them (the group (4) test above);
#   3. the THREE LAYERS hold — a verifier module reaches leaves and nothing in
#      the lifecycle/presentation layer, with ONE named exception, and a
#      lifecycle module reaches NO verifier module at all;
#   4. every shipped orchestration module has a test module that imports it.
#
# ON THE ASYMMETRY BETWEEN THOSE TWO CLAUSES, because D-193 was half a prose
# defect (lead ruling, GRIND cycle 7). The EXCEPTION in the verifier direction
# is `_VERIFIER_TO_LIFECYCLE_SEAM`: the walk SKIPS it, the edge is legal, GI-033
# names it, and it never enters any assertion's subject. The lifecycle direction
# has no counterpart and never had a legal one — it asserts `offenders == []`
# outright. It reached that state the long way: an allowlist stood there
# (C-054), then a second frozenset consulted with `continue` (D-193), then a
# roster the assertion COMPARED against rather than skipped on while D-192's one
# real crossing was open. All three are deleted, the last of them by the
# reshaping that emptied it. There is no fourth: a crossing nobody can close
# this cycle is a failing test and a filed concern, and
# `test_the_layering_rule_consults_exactly_one_exception_table` is what keeps
# one from being written back in.
# --------------------------------------------------------------------------- #

#: GI-033's three layers, by module basename.
#:
#: LEAF is what a verifier may always reach: the artifact primitives, the state
#: readers, the vocabulary and the schemas. VERIFIER is the set whose diff makes
#: `verifier_touched` fire — the gate ladder, the phase transitions, the INSPECT
#: width decision and the evidence sweep — and it is exactly the set casting
#: 10's `VERIFIER_PATH_PATTERNS` narrowed to. Everything else is
#: LIFECYCLE/PRESENTATION, `halt.py` INCLUDED: GI-033 puts it outside the
#: verifier set by name, and the transitions reach it through one seam.
#: THE LEAF SET IS A CHECKED PROPERTY, NOT A LIST (fallout GI-033 / AC-061,
#: ruling `lead_ruling_gi_033_escalation_leaf`).
#:
#: GI-033 names four leaf modules parenthetically — artifacts, foundry_state,
#: vocab, schemas — and this set has carried `findings`, `citation` and
#: `validation` beside them since the split, because what makes a module a leaf
#: is not its name: it is that it imports only stdlib, `schemas/`, and other
#: leaves, AT ANY DEPTH. `test_every_leaf_module_imports_only_leaves` asserts
#: exactly that for every member below, so this cannot become an allowlist by
#: someone adding a name to it — a module that reaches the lifecycle layer fails
#: the property and the addition fails with it.
#:
#: `escalation` is the eighth member and the first from inside
#: `tools/orchestration/`. It qualified by having its ONE disqualifying reach
#: INVERTED: it used to import `directives.py` lazily for the directive bodies,
#: and now it owns the directive grammar and the block parse — both pure over
#: text — and `directives.py` reads them from it. That is what turned
#: `gates -> escalation` and `transitions -> escalation` from the last two
#: layering violations into verifier-to-leaf edges, which the rule has never had
#: anything to say about.
#: `worktree_helpers` is the ninth member and it qualified on the property
#: alone: it imports `fcntl`, `os`, `shutil`, `signal`, `subprocess`,
#: `threading`, `time` and `pathlib` and NOTHING under `foundry_mcp` at any
#: depth. It joins because `evidence.py` — a verifier module as of this cycle —
#: reads its worktree and subprocess primitives at module top, and a leaf is
#: what both layers may reach. Spawning subprocesses does not disqualify a
#: module the way reaching the lifecycle layer does: the rule is about the
#: dependency graph, and this module's is empty.
_LEAF_MODULES = frozenset({
    "artifacts", "foundry_state", "vocab", "findings", "citation", "validation",
    "escalation", "worktree_helpers",
    # fallout FR-009 / GI-033 (D-170, casting 7's concern C-079) — THE NINTH,
    # AND IT WAS ADDED BECAUSE THE ARITHMETIC BELOW LEFT NO OTHER MOVE.
    #
    # What a `key_files` entry IS — a file path, or a directory spelled with a
    # trailing slash covering everything beneath it — is read by
    # `transitions.py` and `width.py` (VERIFIER) and by `directives.py` and
    # `foundry_spawn.py` (lifecycle). The two layers are mutually unreachable at
    # module top, so a verifier site cannot read casting 7's statement of the
    # same rule in `foundry_validate.py` however convenient that would be, and
    # this guard's own sentence gives the remedy: "a symbol read from BOTH can
    # live only in a leaf". `orchestration/escalation.py` is the precedent for a
    # leaf living inside this package. `keyfiles.py` imports NOTHING, which is
    # the strongest form of the property this set is checked on, and
    # `test_keyfiles.py#test_this_module_imports_nothing_which_is_what_makes_it_a_leaf`
    # asserts it on the source rather than leaving it to this roster.
    "keyfiles",
})

#: The four GI-033 names in its own parenthetical. They are leaves BY THE
#: INVARIANT, not by anything this guard measures, so the property assertion
#: below reports on the other four and not on these.
#:
#: THE DISTINCTION IS NOT ACADEMIC, and it is recorded here rather than
#: discovered later: `artifacts.py` reaches `tools/foundry_spawn.py` lazily
#: inside `_declared_external_inputs` — the same shape that disqualified
#: `escalation.py` until this cycle inverted it. GI-033 names artifacts a leaf,
#: so that reach is not a violation of the rule as written; it is the reason
#: nobody should read "leaf" as "pure" without checking which half of this set
#: a module is in.
_INVARIANT_NAMED_LEAVES = frozenset({"artifacts", "foundry_state", "vocab", "schemas"})

#: fallout AC-061 / FR-063 / GI-033 (D-126, casting 5's concern C-067) — THE
#: FIFTH VERIFIER MODULE, AND IT DOES NOT LIVE IN THIS PACKAGE.
#:
#: This set was the four `orchestration/` modules, and the spec's dependency-flow
#: paragraph names FIVE: "verifier (gates.py, transitions.py, width.py,
#: evidence_boundary.py, evidence.py)". `vocab.VERIFIER_PATH_PATTERNS` matches
#: `tools/evidence.py` too, so the guard and the width rule disagreed about what
#: the verifier layer IS.
#:
#: TWO LIVE CONSEQUENCES, both closed here. (1) The verifier-side walks iterated
#: `_shipped_orchestration_modules()`, so `tools/evidence.py`'s OUTGOING edges
#: were judged by nothing — driven by planting a module-top `import
#: foundry_mcp.tools.display` at the top of evidence.py in an isolated copy: all
#: three layering tests PASSED, and a lazy `evidence -> report_seal` plant was
#: equally invisible, while the same plant naming `orchestration.gates` failed
#: correctly. The walk worked; its SUBJECT SET was the hole, and both walks are
#: stated over `_layered_modules()` now. (2) The lifecycle-direction walk
#: classified `evidence` as lifecycle BY ELIMINATION, so a lifecycle module
#: importing it was permitted — the direction AC-061 says is refused ENTIRELY.
#:
#: `halt.py`'s exclusion is unchanged and is asserted below: GI-033 puts it
#: outside the verifier set by name, and that is why the seam exists.
_VERIFIER_MODULES = frozenset({
    "gates", "transitions", "width", "evidence_boundary", "evidence",
})

#: The ONE exception GI-033 names, spelled as the edge it permits.
#:
#: `transitions.py` dispatches the `halt` token and the terminal seal into
#: `halt.py`, and nothing flows back. It is one-way and it is enumerated, which
#: is what makes it an exception rather than a hole: `halt.py` importing a
#: verifier module fails the rule exactly as any other lifecycle module does.
_VERIFIER_TO_LIFECYCLE_SEAM = frozenset({("transitions", "halt")})

#: fallout AC-015 / AC-061 / FR-063 / GI-033 / OT-015 (D-193, from D-126) —
#: `_UNCLOSED_CROSS_PACKAGE_EDGES` STOOD HERE, AND THE SECOND EXCEPTION TABLE IS
#: GONE WITH IT.
#:
#: It was a two-row frozenset holding `evidence -> foundry_handoff` (module top)
#: and `foundry_handoff -> evidence` (lazy, inside `foundry_accept_casting`) —
#: the tree's only two live crossings — and all THREE layering assertions
#: consulted it with `if (name, imported) in _UNCLOSED_CROSS_PACKAGE_EDGES:
#: continue`. So the guard reported a clean tree over a tree that had both.
#:
#: The argument for it was that an EXACT-EQUALITY roster differs from the
#: fail-open dict C-054 deleted: a stale row failed, a new crossing failed, and
#: a maintaining test — `test_every_unclosed_crossing_still_exists_and_is_
#: somebody_elses`, deleted with the table — kept the rows on files another
#: casting owned. That was all true and it was not enough. The header at
#: the top of this section says the layering holds "with ONE named exception",
#: and `test_no_lifecycle_module_reaches_a_verifier_module_at_any_depth` says in
#: its own docstring that "this rule takes no exception table at all" — three
#: lines above the skip that consulted the second one, and directly above a
#: failure message repeating the absolute claim. Two tables under prose that
#: promises one is a guard a reader cannot check against the code, which is the
#: whole reason NFR-009 keeps this rule in forty lines of `ast` rather than in a
#: config file.
#:
#: ONE ROW WAS CLOSED AND THE OTHER TURNED OUT NOT TO BE A ROW AT ALL. Casting 5
#: removed `evidence.py`'s module-top import of `foundry_handoff` (D-191) along
#: the line C-067 named — "a symbol read from BOTH can live in neither, it
#: belongs in a leaf" — and the declaration grammar went to `artifacts.py`. The
#: other direction does not answer to that arithmetic: casting 7 drove it for
#: D-192 and reported in C-107 that what `foundry_accept_casting` reaches is not
#: a symbol but the evidence ENGINE (`verify_evidence`, one caller, ~800 lines
#: of closure shared with the sweep), so there was nothing to move to a leaf and
#: the edge could only be REVERSED or ELIMINATED — a four-casting reshaping no
#: cycle of this run had scoped when this paragraph was written.
#:
#: IT WAS SCOPED AND THEN IT WAS DONE, IN GRIND CYCLE 7. The crossing was first
#: RECORDED where it could not be mistaken for absence — an open-violations
#: roster below the lifecycle walk, which the assertion COMPARED ITS RESULT
#: AGAINST rather than skipped on, so the edge stayed inside `offenders` and a
#: reader of either could see the tree was not clean. Then casting 7 reversed
#: it: `foundry_accept_casting` moved into `tools/evidence.py` beside the engine
#: it runs, with its lifecycle dependencies hoisted into `artifacts.py`, and
#: casting 2 repointed the registrar and this file's four accept-casting pins in
#: the same dispatch per GI-026. `offenders` emptied, the roster went stale, and
#: the assertion failed by name until the roster was deleted — which is the
#: behaviour it was built for. What is left as an EXCEPTION anywhere in this
#: file is the one GI-033 itself names, spelled once, one-way, and asserted to
#: exist.
#:
#: fallout GI-033 / AC-061 / FR-063 / OT-015 (D-021 / D-035) — THERE IS NO
#: LAYERING-DEBT ALLOWLIST ANY MORE, AND THAT IS THE FIX.
#:
#: It was a dict of `(home, imported) -> reason` that the two assertions below
#: consulted BEFORE judging an edge, so `test_the_three_layers_hold_...`
#: reported `violations == []` against a tree in which fourteen module-top
#: crossings broke the rule it is named for. Every row was a real violation
#: wearing an explanation, which is what made it a HOLLOW guard rather than a
#: lenient one: the assertion passed, so nothing downstream could tell the tree
#: had the coupling.
#:
#: The rule is asserted DIRECTLY now. The only exception the scan honours is
#: `_VERIFIER_TO_LIFECYCLE_SEAM` above — GI-033's own named seam, one edge,
#: one-way — and an edge that is not that seam FAILS, whatever reason anyone
#: could write beside it. A row that cannot be closed is a failing test and a
#: filed concern, never a table entry: the arithmetic is that the two layers
#: are mutually unreachable at module top, so a symbol read from BOTH can live
#: only in a leaf, and which leaf is a question for the casting that owns it.
#:
#: That sentence was written here and then stopped being true for four cycles,
#: which is the whole of D-193: `_UNCLOSED_CROSS_PACKAGE_EDGES` was added above
#: with a careful argument for why it was a different KIND of table, and three
#: assertions skipped on it while this paragraph and two docstrings went on
#: promising one exception. The claim and the code agree again, and the way they
#: were made to agree was closing the rows, not re-wording the promise.


# fallout FR-004 / AC-013 / OT-012 — THE REPOINT ROSTER IS GONE, AND THAT IS
# THE HANDSHAKE BY WHICH IT ENDED.
#
# It carried ten test modules another casting owned, each still importing the
# module casting 2 deleted, each named with its owner. The table's own rule was
# that it could only SHRINK: an entry whose module had repointed failed, so
# nobody could leave a row behind. Castings 3, 4, 5, 6 and 12 have now
# repointed all ten, every row went stale at once, and the tuple is gone with
# them — which is what the rule was for.
#
# What replaces it is the absolute assertion it was standing in for, and that
# assertion is now TRUE of the whole tree rather than of `src/` alone. OT-012
# says "no module named foundry_orchestrator exists and NOTHING imports it";
# an inventory was the honest way to say "not yet" while the repoints were in
# flight, and keeping one after they land would be an allowlist for a debt that
# no longer exists — which is why the empty tuple went with the rows rather
# than staying behind to say the same thing in a shape nothing distinguishes
# from an exception table. The debt register further down this module was
# retired on the identical ruling; both are re-creatable from this file's
# history the day another episode needs one.


def _orchestration_dir() -> Path:
    return Path(artifacts.__file__).resolve().parent / "orchestration"


def _shipped_orchestration_modules() -> list[Path]:
    return sorted(
        p for p in _orchestration_dir().glob("*.py") if p.name != "__init__.py"
    )


def _module_top_imports(path: Path) -> set[str]:
    """The orchestration modules `path` imports AT MODULE TOP.

    A function-local import is NOT an edge here, and that is the whole point of
    the lazy seam: `report_seal.py` reaching `gates.py` inside one function does
    not make the package cyclic at import time, and the comment at each seam
    says which cycle it avoids. What this scan measures is what Python actually
    executes when the package loads.
    """
    return {
        module.rsplit(".", 1)[-1]
        for module in _module_top_dotted_imports(path)
        if module.startswith("foundry_mcp.tools.orchestration.")
    }


def _package_root() -> Path:
    """`foundry_mcp/` itself — the parent of `tools/`, derived from a leaf."""
    return Path(artifacts.__file__).resolve().parent.parent


def _submodules_named_by(dotted: str, names: list[str]) -> set[str]:
    """Which of `names` are SUBMODULES of the package `dotted`, on disk.

    fallout AC-061 / FR-063 / GI-033 / AC-015 / GI-025 / OT-015 (D-192, concern
    C-107) — THE THIRD SPELLING, AND BOTH WALKS WERE BLIND TO IT.

    An import has three spellings and this guard resolved two. Both walks below
    read an `ast.ImportFrom` by the basename of `node.module`, so
    `from foundry_mcp.tools.evidence import verify_evidence` resolved to
    `evidence` and `import foundry_mcp.tools.evidence` resolved to `evidence`,
    while `from foundry_mcp.tools import evidence` — the same module, the same
    load, written the way Python's own tutorial writes it — resolved to `tools`
    and matched nothing. Driven: `_all_imports` over a one-line module in each
    spelling returned `hits_verifier=['evidence']` twice and `[]` for the third,
    and the same source written `from foundry_mcp.tools.orchestration import
    gates` was invisible to the ACYCLICITY check too, because
    `_module_top_imports` filters on the prefix `foundry_mcp.tools.
    orchestration.` and what it was handed had no trailing segment.

    So AC-061's "refuses lifecycle-to-verifier imports ENTIRELY" held in two
    spellings out of three, and the direction that takes no exception at any
    depth took one at a punctuation mark. That is D-081's class exactly — its
    heading is "`ast.Import` IS A MODULE-TOP IMPORT, AND THE SCAN COULD NOT SEE
    ONE" — recurring in the spelling that fix did not enumerate, which is why
    the resolution lives HERE, in one helper both walks call, rather than as a
    third arm copied into each.

    RESOLVED ON DISK, NEVER BY NAME. Collecting every alias of every
    `ImportFrom` would catch the spelling and invent crossings with it:
    `from foundry_mcp.schemas.vocab import DEFECT_TIERS` would read a frozenset
    as a module, and any symbol sharing a basename with a module would be a
    phantom edge. A guard that reports edges the tree does not have is a guard
    somebody adds an exception table to, which is the shape this file has now
    deleted two of. `<package>/<name>.py` existing is a fact, so an alias is a
    module edge when it IS one and a symbol otherwise.

    Relative imports are left to the caller's existing reading: `node.level` is
    non-zero for those, `node.module` is then a suffix rather than a package
    path, and this package writes none. Widening the walk is additive here or
    it is not worth doing.
    """
    if not dotted.startswith("foundry_mcp"):
        return set()
    package = _package_root().joinpath(*dotted.split(".")[1:])
    return {
        name
        for name in names
        if (package / f"{name}.py").is_file()
        or (package / name / "__init__.py").is_file()
    }


def _module_top_dotted_imports(path: Path) -> set[str]:
    """Every dotted module name `path` imports AT MODULE TOP, all three
    spellings.

    THE THIRD IS RESOLVED BY `_submodules_named_by` ABOVE, and it was missing
    from the two this docstring used to promise: `from foundry_mcp.tools.
    orchestration import gates` is a module-top import of `gates` that this
    walk reported as `foundry_mcp.tools.orchestration`, so the acyclicity check
    and the three-layer scan both looked straight past it (D-192). The header
    below is the same class in its first spelling.

    fallout AC-015 / FR-006 / GI-025 / OT-015 (D-081) — `ast.Import` IS A
    MODULE-TOP IMPORT, AND THE SCAN COULD NOT SEE ONE.

    Both callers of this walk tested `isinstance(node, ast.ImportFrom)` and
    nothing else, so `import foundry_mcp.tools.orchestration.zz_b` — a real
    import Python executes at package load — was invisible to the acyclicity
    check AND to the layering scan. Driven on an isolated copy: two modules
    importing each other in the `ast.Import` spelling gave `pytest -k acyclic`
    ONE PASSED, while the identical cycle written `from ... import ...` failed
    correctly and named it. A second plant put a module-top
    `import foundry_mcp.tools.display` at the top of `gates.py` and the seam
    test stayed green over an edge GI-033's violation column names by hand.

    The helper's own docstring claimed "what this scan measures is what Python
    actually executes when the package loads", which was the promise and not the
    behaviour. Both spellings are walked now, in ONE place, so the two callers
    cannot drift into seeing different halves of the same statement.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            if not node.level:
                out.update(
                    f"{node.module}.{name}"
                    for name in _submodules_named_by(
                        node.module, [alias.name for alias in node.names]
                    )
                )
        elif isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
    return out


def _module_top_package_imports(path: Path) -> set[str]:
    """The NON-orchestration package modules `path` imports AT MODULE TOP.

    fallout AC-014 / D-036 — THE WINDOW WAS NARROWER THAN THE RULE.

    `_module_top_imports` above answers only about `tools.orchestration.*`, so
    an edge that left the package was invisible to the layering scan however
    plainly it broke the rule: `transitions.py` (verifier) imports
    `tools/concerns.py` at module top, and `concerns.py` imports
    `tools/foundry.py`, so the verifier layer reaches the largest lifecycle
    module in the tree through an edge no assertion looked at.

    LEAF MODULES ARE EXCLUDED, because they are the layer every module may
    import — that is what makes them leaves — and the existence of each one is
    asserted beside the layering rule so this cannot be stated over a name that
    has moved. Everything else under `foundry_mcp.tools` and
    `foundry_mcp.parsers` is lifecycle by elimination.
    """
    out: set[str] = set()
    for module in _module_top_dotted_imports(path):
        if module.startswith("foundry_mcp.tools.orchestration"):
            continue
        if not module.startswith(("foundry_mcp.tools.", "foundry_mcp.parsers.")):
            continue
        stem = module.rsplit(".", 1)[-1]
        if stem in _LEAF_MODULES:
            continue
        out.add(stem)
    return out


def _all_imports(path: Path, also_by_name: tuple[str, ...] = ()) -> set[str]:
    """Every module `path` names in an import, at ANY depth, by basename.

    ALL THREE SPELLINGS, through `_submodules_named_by` — the same helper the
    module-top walk calls, stated once so the two directions of the layering
    rule cannot come to see different halves of one statement, which is the
    drift D-081 was filed for over one rule and D-192 found again over the
    spelling that fix did not enumerate. `from foundry_mcp.tools import
    evidence` is a lifecycle module reaching the verifier layer as plainly as
    the two forms above it, and this walk is what AC-061's "at no depth" is
    asserted by.

    fallout FR-004 / GI-010 / AC-013 / OT-012 (D-198) — `also_by_name` IS THE
    READING FOR A MODULE THAT MUST NOT EXIST.

    Disk resolution answers "is this alias a module" by asking whether
    `<package>/<name>.py` is there, and that is the right question for a
    layering edge: the module exists, the only doubt is whether the alias names
    it or a symbol. It is the WRONG question for `foundry_orchestrator`, whose
    whole requirement is that the file is gone — the resolution would then
    return nothing and the guard would see an importer only while the forbidden
    artifact was present, which is to say only when it was already too late to
    be the strong half of OT-012. A guard conditional on the thing it guards
    against is D-198's shape exactly.

    So the caller may NAME the modules to read by alias instead. Not a widening
    of the default: `also_by_name` is empty for both layering walks and they are
    bit-identical to before. What makes it safe here and unsafe as a blanket
    rule is that the caller asserts these strings ARE module names, so the
    phantom crossing the docstring above warns about — a `DEFECT_TIERS` read as
    a module — cannot be constructed unless somebody names a symbol after the
    monolith this package deleted, which is itself the facade GI-010 refuses.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    named = frozenset(also_by_name)
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module.rsplit(".", 1)[-1])
            if not node.level:
                out |= _submodules_named_by(
                    node.module, [a.name for a in node.names]
                )
                out |= {a.name for a in node.names} & named
        elif isinstance(node, ast.Import):
            out.update(a.name.rsplit(".", 1)[-1] for a in node.names)
    return out


def test_the_orchestration_import_graph_is_acyclic_at_module_top():
    """fallout AC-015 / OT-015 — a cycle takes every tool in the server down.

    Not a style rule: `from x import y` inside a cycle raises ImportError at the
    first import, and this package is loaded by an MCP server whose every tool
    lives behind that import. The failure is total and it is at startup.
    """
    modules = _shipped_orchestration_modules()
    assert len(modules) >= 13, [p.name for p in modules]

    graph = {p.stem: _module_top_imports(p) for p in modules}
    assert any(graph.values()), (
        "no module-top orchestration import found at all; the scan has gone "
        "blind and the walk below proves nothing"
    )

    colour: dict[str, int] = {}
    cycles: list[list[str]] = []

    def walk(node: str, stack: list[str]) -> None:
        colour[node] = 1
        for peer in sorted(graph.get(node, ())):
            if colour.get(peer) == 1:
                cycles.append(stack[stack.index(peer):] + [peer])
            elif colour.get(peer, 0) == 0:
                walk(peer, stack + [peer])
        colour[node] = 2

    for name in sorted(graph):
        if colour.get(name, 0) == 0:
            walk(name, [name])
    assert cycles == [], (
        f"import cycle(s) among the orchestration modules: {cycles}. Break the "
        "back edge with a named lazy seam inside the calling function — the "
        "shape every existing seam in this package uses, with a comment saying "
        "which cycle it avoids."
    )


def test_a_planted_cycle_fails_the_rule_by_name(tmp_path):
    """The anchor: the walk above finds a cycle when there IS one.

    An acyclicity check over a clean graph is green whether it works or not, so
    the recogniser is driven on a graph built to contain one.
    """
    graph = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": set()}
    colour: dict[str, int] = {}
    found: list[list[str]] = []

    def walk(node, stack):
        colour[node] = 1
        for peer in sorted(graph.get(node, ())):
            if colour.get(peer) == 1:
                found.append(stack[stack.index(peer):] + [peer])
            elif colour.get(peer, 0) == 0:
                walk(peer, stack + [peer])
        colour[node] = 2

    for name in sorted(graph):
        if colour.get(name, 0) == 0:
            walk(name, [name])
    assert found, "the cycle recogniser cannot see a three-node cycle"


def test_the_three_layers_hold_with_exactly_one_named_seam():
    """fallout GI-033 / AC-061 / OT-015 — the layering, both directions.

    A VERIFIER module reaches leaf modules and nothing in the lifecycle layer,
    with one enumerated exception: `transitions.py` dispatches the halt token
    and the terminal seal into `halt.py`, one-way. A LIFECYCLE module reaches no
    verifier module AT ALL — that direction has no exception, because a
    lifecycle module that can call a gate is a lifecycle module that can decide
    a width, and the whole point of narrowing `VERIFIER_PATH_PATTERNS` is that
    the set which forces FULL width is small and stated.
    """
    # fallout AC-061 (D-126) — EVERY LAYERED MODULE, not the orchestration
    # package alone. `tools/evidence.py` is a verifier module and lives outside
    # this package, so a scan over `orchestration/*.py` judged its edges by
    # nothing at all while stating a rule about the verifier layer.
    modules = sorted(_layered_modules().values())
    names = {p.stem for p in modules}
    assert _VERIFIER_MODULES <= names, sorted(_VERIFIER_MODULES - names)
    # The leaf set is a real set of real modules: a layering rule stated over a
    # name that has moved is a rule that judges nothing.
    tools = Path(artifacts.__file__).resolve().parent
    schemas = tools.parent / "schemas"
    for leaf in sorted(_LEAF_MODULES):
        assert (
            (tools / f"{leaf}.py").exists()
            or (schemas / f"{leaf}.py").exists()
            # ...and a leaf may live INSIDE the orchestration package: what makes
            # one is the property asserted above, not the directory it sits in.
            or (tools / "orchestration" / f"{leaf}.py").exists()
        ), leaf

    violations: list[str] = []
    checked = 0
    for path in modules:
        home = path.stem
        for imported in sorted(
            _module_top_imports(path) | _module_top_package_imports(path)
        ):
            checked += 1
            if (home, imported) in _VERIFIER_TO_LIFECYCLE_SEAM:
                continue
            if imported in _LEAF_MODULES:
                # A leaf is the layer BOTH sides may reach; that is the whole of
                # what "leaf" means, and the property is asserted above rather
                # than assumed.
                continue
            if home in _VERIFIER_MODULES and imported not in _VERIFIER_MODULES:
                violations.append(
                    f"{home} (verifier) imports {imported} (lifecycle) at module top"
                )
            if home not in _VERIFIER_MODULES and imported in _VERIFIER_MODULES:
                violations.append(
                    f"{home} (lifecycle) imports {imported} (verifier) at module top"
                )
    assert checked >= 10, (
        f"only {checked} orchestration import edge(s) seen; the scan is blind"
    )
    assert violations == [], (
        f"layering violation(s): {violations}. The symbol belongs in a leaf — "
        "`artifacts`, `foundry_state`, `vocab` or `schemas` — because the two "
        "layers are mutually unreachable at module top and a symbol read from "
        "BOTH can live in neither. There is no table to record an exception in: "
        "the one exception GI-033 names is the transitions-to-halt seam above, "
        "and an edge that is not it fails here however good the reason is."
    )

    # ...and `halt.py` is OUTSIDE the verifier set, which is the half of GI-033
    # a reader is most likely to get backwards: the seam exists BECAUSE halt is
    # lifecycle, not despite it.
    assert "halt" not in _VERIFIER_MODULES


def test_every_leaf_module_imports_only_leaves():
    """fallout GI-033 / AC-061 / OT-015 — THE LEAF SET IS A PROPERTY.

    `_LEAF_MODULES` is what both layers may reach, so every exception the
    layering rule grants rests on it. A NAME LIST WOULD BE AN ALLOWLIST: adding
    a module to it would excuse every edge into that module without anyone
    having to show the module deserves it, which is exactly the shape the
    deleted layering-debt table had and exactly why it is gone.

    So membership is asserted rather than declared. A leaf may import stdlib,
    `foundry_mcp.schemas.*`, and other leaves — AT ANY DEPTH, module-top and
    lazy alike, because a function-local import is still a dependency and the
    layering rule this set feeds is asserted at both depths too. A leaf that
    reaches the lifecycle or verifier layer fails HERE, before its edges are
    excused anywhere else.

    THE DEPTH IS THE POINT, and `escalation.py` is why it is written down. Its
    module-top imports were vocab, artifacts and foundry_state — leaves, all
    three — so by a module-top reading it had looked like a leaf for the whole
    split. One LAZY import of `orchestration/directives.py` sat inside
    `_directives_text`, and directives is a lifecycle module GI-033's violation
    column names outright. Judged at module top it would have joined the set on
    a false premise and excused two real crossings; judged at every depth it was
    refused until the reach was inverted.
    """
    tools = Path(artifacts.__file__).resolve().parent
    schemas = tools.parent / "schemas"
    parsers = tools.parent / "parsers"

    # `foundry_mcp.parsers.*` is BELOW the layered set, and that is asserted
    # rather than assumed: a parser that imported `tools` would be a lifecycle
    # dependency wearing a lower-layer name.
    for parser in sorted(parsers.glob("*.py")):
        if parser.name == "__init__.py":
            continue
        reached = _all_imports(parser)
        assert not (reached & {p.stem for p in tools.glob("*.py")}), (
            f"{parser.name} reaches tools/: {sorted(reached)}"
        )

    homes: dict[str, Path] = {}
    for leaf in sorted(_LEAF_MODULES):
        for candidate in (
            tools / f"{leaf}.py",
            schemas / f"{leaf}.py",
            tools / "orchestration" / f"{leaf}.py",
        ):
            if candidate.exists():
                homes[leaf] = candidate
                break
    assert sorted(homes) == sorted(_LEAF_MODULES), sorted(set(_LEAF_MODULES) - set(homes))

    offenders: list[str] = []
    checked = 0
    for leaf, path in sorted(homes.items()):
        if leaf in _INVARIANT_NAMED_LEAVES:
            continue
        for imported in sorted(_all_imports(path)):
            checked += 1
            if imported in _LEAF_MODULES:
                continue
            # `schemas/` in full — the vocabulary and the JSON schemas are the
            # layer GI-033 names beside the leaf modules themselves.
            if (schemas / f"{imported}.py").exists():
                continue
            # Anything outside the plugin's own package is stdlib or a
            # dependency, and neither has a layer in this rule.
            if (parsers / f"{imported}.py").exists():
                continue
            if not (
                (tools / f"{imported}.py").exists()
                or (tools / "orchestration" / f"{imported}.py").exists()
            ):
                continue
            offenders.append(f"{leaf} -> {imported}")
    assert checked > 10, (
        f"only {checked} import(s) seen across the property-checked leaves; the "
        "scan has gone blind and the assertion below proves nothing"
    )
    assert offenders == [], (
        f"leaf module(s) reaching a non-leaf: {offenders}. A leaf is what BOTH "
        "layers may import, so one that depends on the lifecycle or verifier "
        "layer hands every importer a transitive dependency on that layer. "
        "Either invert the reach — the module that owns the file calls the leaf, "
        "never the reverse — or take the module out of _LEAF_MODULES and let its "
        "edges be judged as the crossings they are."
    )




def test_a_planted_lifecycle_reach_disqualifies_a_leaf(tmp_path):
    """The anchor: the property check above recognises a leaf that is not one.

    A scan over a clean set is green whether it works or not, so the recogniser
    is driven over a module built to fail it — the exact shape `escalation.py`
    had before the inversion, a leaf-looking module with one LAZY reach into a
    lifecycle module buried inside a function.
    """
    planted = tmp_path / "pretend_leaf.py"
    planted.write_text(
        "from foundry_mcp.tools.artifacts import _load_json\n"
        "def f(project_root):\n"
        "    from foundry_mcp.tools.orchestration.directives import _read_directives\n"
        "    return _read_directives(project_root)\n",
        encoding="utf-8",
    )
    reached = _all_imports(planted)
    assert "directives" in reached, sorted(reached)
    assert "directives" not in _LEAF_MODULES
    # ...and the module-top-only view is what would have MISSED it, which is the
    # reason the check above walks every depth.
    assert "directives" not in _module_top_imports(planted)




def test_the_seam_table_names_an_edge_that_exists():
    """A permitted exception that permits nothing is an exception nobody
    removed. Every entry in the seam table must name a real module-top edge, or
    the table has outlived the coupling it records."""
    modules = {p.stem: p for p in _shipped_orchestration_modules()}
    for home, imported in sorted(_VERIFIER_TO_LIFECYCLE_SEAM):
        assert home in modules and imported in modules, (home, imported)
        assert imported in _module_top_imports(modules[home]), (
            f"the seam {home} -> {imported} is declared and does not exist. "
            "Delete the entry: an allowlist that survives what it excuses is "
            "the boundary-moving shape this rule exists to stop."
        )


def test_no_verifier_module_reaches_a_lifecycle_module_lazily_either():
    """The seam is ONE-WAY and it is the only one, at any depth.

    A lazy import is still a reach: `gates.py` calling into `guidance.py` inside
    a function couples the gate to the presentation layer just as firmly as a
    module-top import would, and it does it where no import scan looks. So the
    rule is asserted over EVERY import in the file, with the same one exception.
    """
    # fallout AC-061 (D-126) — EVERY layered module both sides, so a verifier
    # module outside `orchestration/` is walked and a lifecycle module outside
    # it can be reached INTO. `evidence.py` was neither before this.
    layered = _layered_modules()
    lifecycle = {
        name for name in layered
        if name not in _VERIFIER_MODULES and name not in _LEAF_MODULES
    }
    offenders: list[str] = []
    for name, path in sorted(layered.items()):
        if name not in _VERIFIER_MODULES:
            continue
        for imported in sorted(_all_imports(path) & lifecycle):
            if (name, imported) in _VERIFIER_TO_LIFECYCLE_SEAM:
                continue
            offenders.append(f"{name} -> {imported}")
    assert offenders == [], (
        f"verifier module(s) reaching the lifecycle layer: {offenders}. Either "
        "the symbol belongs in a leaf, or the edge is a new seam and belongs in "
        "_VERIFIER_TO_LIFECYCLE_SEAM with the reason it cannot be avoided."
    )


def _layered_modules() -> dict[str, Path]:
    """Every module GI-033 assigns a layer to, by basename.

    `tools/*.py` and `tools/orchestration/*.py`. The invariant's three layers
    are stated over this package — leaf, verifier, lifecycle/presentation — and
    `foundry_mcp/server.py` is deliberately NOT in it: the registrar binds every
    door in `_DISPATCH` and reaching a gate is its whole job, so judging it as a
    lifecycle module would make the transport layer the rule's first offender.
    `parsers/` is not in it either, and it is not unjudged:
    `test_every_leaf_module_imports_only_leaves` asserts the stronger property
    that a parser reaches NOTHING under `tools/` at all.
    """
    tools = Path(artifacts.__file__).resolve().parent
    out: dict[str, Path] = {}
    for path in sorted(
        list(tools.glob("*.py")) + list((tools / "orchestration").glob("*.py"))
    ):
        if path.name == "__init__.py":
            continue
        out[path.stem] = path
    return out


#: fallout GI-033 / AC-061 / FR-063 / OT-015 (D-080) — THE VACUITY GUARD FOR THE
#: LIFECYCLE DIRECTION, STATED AS MODULES RATHER THAN AS A COUNT.
#:
#: This suite's own rule, learned on C-062 and written down at
#: `test_inspect_mode.py`'s width pin: "named modules rather than a count alone,
#: because a count passes on any roster of the right size". The assertion below
#: only judges what its scan managed to SEE, so a scan that has gone blind is
#: indistinguishable from a clean tree unless the modules it must see are named.
#:
#: These are the fourteen lifecycle/presentation modules whose crossings the
#: rule exists for: the eight in `tools/orchestration/` that are neither leaf
#: nor verifier, plus the six under `tools/` that the survey's section map and
#: GI-033's violation column name by hand — display, the report seal's writer,
#: the spawn surface, the concern and roster ledgers and the largest lifecycle
#: module in the tree. It is a FLOOR, never the roster: the set the scan judges
#: is derived by `_layered_modules`, so a module the package gains is judged the
#: day it lands whether or not anyone adds it here.
_LIFECYCLE_FLOOR = frozenset({
    "directives", "fix_gate", "guidance", "halt", "report_seal", "spend",
    "streams", "teams",
    "concerns", "display", "foundry", "foundry_report", "foundry_spawn",
    "rosters",
    # fallout AC-061 (D-126, then D-192): the module that HELD the acceptance
    # door, named because the widened verifier set made its reach into
    # `tools/evidence.py` judgeable for the first time. That reach is gone —
    # D-192 moved `foundry_accept_casting` to the verifier layer beside the
    # engine it runs — and the name STAYS, because this is a vacuity floor and
    # not a debt list: `foundry_handoff` is still a lifecycle module, and a
    # walk that stopped seeing it would be a walk gone blind over the module
    # this rule's only real crossing came out of.
    "foundry_handoff",
})

#: fallout AC-015 / AC-061 / FR-063 / GI-033 / OT-015 (D-192 / D-193, concern
#: C-107) — `_OPEN_LAYERING_VIOLATIONS` STOOD HERE, AND THE WAY IT WENT IS THE
#: POINT.
#:
#: It was `{offender line: who closes it}` carrying ONE row — `foundry_handoff
#: (lifecycle) reaches evidence (verifier)` — and the assertion below COMPARED
#: its result against it rather than skipping on it, which is the whole
#: distinction from the two tables this guard deleted before it. An excused edge
#: never entered `offenders`, so the assertion's subject was `[]` and the guard
#: reported a clean tree over a tree with the coupling in it (C-054's fourteen
#: crossings, then D-193's two). A recorded edge WAS in `offenders`: the walk
#: found it, the assertion named it, and a reader of either could see the tree
#: was not clean.
#:
#: IT WAS EXACT EQUALITY SO IT COULD NOT OUTLIVE THE DEBT, and it did not. Its
#: own paragraph said "the day D-192's reshaping lands, `offenders` empties,
#: this roster goes stale and the assertion fails telling whoever closed it to
#: delete the row", and that is exactly how this deletion was prompted: casting
#: 7 moved `foundry_accept_casting` out of `foundry_handoff.py` into
#: `tools/evidence.py` beside the `verify_evidence` engine it runs, `offenders`
#: went to `[]`, and this file went red naming the stale row. `test_every_open_
#: layering_violation_names_who_closes_it` went with it, on the instruction its
#: own emptiness message carried: "delete it along with the roster and let the
#: layering rule assert `offenders == []` outright, which is where this guard is
#: going."
#:
#: SO THE RULE BELOW IS ABSOLUTE IN ITS OWN TEXT NOW, not merely in its prose.
#: There is no roster, no allowlist and no skip in the lifecycle direction —
#: only `offenders == []` — and the one exception that survives anywhere in this
#: file is `_VERIFIER_TO_LIFECYCLE_SEAM`, which points the other way, is
#: one-way, is enumerated and is asserted to exist. A future crossing has no
#: table to be written into: it is a failing test and a filed concern, which is
#: what C-107 was and what closing it looked like.


def test_no_lifecycle_module_reaches_a_verifier_module_at_any_depth():
    """fallout GI-033 / AC-061 / FR-063 / OT-015 (D-080) — THE OTHER DIRECTION,
    AND IT HAS NO SEAM.

    AC-061 says the guard "refuses lifecycle-to-verifier imports ENTIRELY". The
    two rules above stated it twice and judged it neither time: the three-layer
    scan reads MODULE TOP only, and its any-depth companion walks the VERIFIER
    modules and asks where they reach. Nothing walked the lifecycle layer. A
    lifecycle module could import a gate inside a function and no assertion in
    this file would have anything to say about it, which is how
    `streams.py -> width._unrecorded_width_problem` — a lifecycle door reaching
    into the width DECISION, lazily, for six cycles — sat in a tree whose
    layering guard reported no violations.

    WHY THIS DIRECTION IS ABSOLUTE WHILE THE OTHER HAS ONE EXCEPTION. The seam
    above exists because a transition must be able to SEAL: `transitions.py`
    dispatches the halt token down into `halt.py` and nothing comes back, and it
    is enumerated, one-way and asserted to exist. There is no matching need in
    this direction and there is a matching harm: a lifecycle module that can
    call a gate is a lifecycle module that can decide a width, and the whole
    value of narrowing `VERIFIER_PATH_PATTERNS` is that the set which forces
    FULL width is small, stated, and cannot be reached from the presentation
    layer. So this rule takes no exception table at all — C-054 recorded why the
    layering-debt allowlist above was deleted rather than extended, and a table
    added here would fail open in exactly the same way.

    THAT SENTENCE WAS FALSE WHEN IT WAS WRITTEN, WHICH IS D-193. Three lines
    below it this assertion skipped on `_UNCLOSED_CROSS_PACKAGE_EDGES` — a
    second table, holding the tree's only two live crossings — and the failure
    message below went on saying "not a seam, not a table, not a lazy import, at
    no depth" over a tree that had both. Driven: emptying that frozenset in a
    snapshot copy turned three passing assertions into three failures naming the
    real edges. A guard whose prose a reader cannot check against its own code
    is the failure NFR-009 keeps this rule in forty lines of `ast` to avoid.

    BOTH TABLES ARE GONE AND SO IS THE LAST CROSSING, WHICH IS D-192. D-191
    closed `evidence -> foundry_handoff` by moving the declaration grammar to
    the leaf. `foundry_handoff -> evidence` did not answer to that arithmetic —
    C-107 drove it and found the door reaching the evidence ENGINE rather than a
    symbol, so there was nothing to hoist — and it was RECORDED in an
    open-violations roster this assertion compared against rather than skipped
    on, until casting 7 reversed the edge and moved `foundry_accept_casting`
    into `tools/evidence.py` beside the engine it runs. The roster went stale
    the moment `offenders` emptied, failed here by name exactly as it was built
    to, and was deleted. What this assertion says is now `offenders == []`, with
    no second operand a reader has to go and check.

    AT ANY DEPTH AND IN ALL THREE SPELLINGS, through the same `_all_imports`
    walk the verifier-side companion uses, so the two directions cannot come to
    see different halves of the same statement — which is the drift D-081 was
    filed for one rule over, and which D-192 found again in the spelling that
    fix did not enumerate (`from foundry_mcp.tools import evidence`, resolved to
    `tools` and matching nothing until `_submodules_named_by` was written).

    THE REMEDY IS ALWAYS A LEAF. GI-033's arithmetic is that the two layers are
    mutually unreachable, so a symbol read from BOTH can live in neither: it
    belongs in `artifacts`, `foundry_state`, `vocab` or `schemas`, and which one
    is a question for the casting that owns it.
    """
    layered = _layered_modules()
    assert _VERIFIER_MODULES <= set(layered), sorted(_VERIFIER_MODULES - set(layered))

    lifecycle = {
        name: path
        for name, path in layered.items()
        if name not in _VERIFIER_MODULES and name not in _LEAF_MODULES
    }
    assert _LIFECYCLE_FLOOR <= set(lifecycle), {
        "expected but not judged as lifecycle": sorted(
            _LIFECYCLE_FLOOR - set(lifecycle)
        ),
        "judged": sorted(lifecycle),
    }

    offenders: list[str] = []
    reaching: set[str] = set()
    for name, path in sorted(lifecycle.items()):
        imports = _all_imports(path)
        if imports & set(layered):
            reaching.add(name)
        for imported in sorted(imports & _VERIFIER_MODULES):
            offenders.append(f"{name} (lifecycle) reaches {imported} (verifier)")

    # ...and the walk is SEEING the package, not returning empty sets. Named
    # modules again: these five each reach a peer under `tools/` in the shipped
    # tree, so a walk that reports none of them has gone blind rather than found
    # a package with no edges in it.
    assert {"guidance", "streams", "teams", "foundry_spawn", "display"} <= reaching, (
        "the lifecycle-side walk sees no package edge from module(s) that have "
        f"one — reaching={sorted(reaching)}. A scan that cannot see an import "
        "cannot judge it, and the assertion below would pass over any tree."
    )

    assert offenders == [], (
        f"lifecycle module(s) reaching the verifier layer: {offenders}. This "
        "direction has NO exception — not a seam, not a table, not a roster, "
        "not a lazy import, at no depth and in no spelling. The remedy is one "
        "of two, and neither of them is a row somebody writes here: the symbol "
        "both layers read belongs in a leaf (`artifacts`, `foundry_state`, "
        "`vocab` or `schemas`), and where what is reached is not a symbol but a "
        "whole engine, the edge is REVERSED — which is how D-192 was closed, "
        "by moving the acceptance door to `tools/evidence.py` beside the "
        "verification it runs. Three tables have now stood where this sentence "
        "is (C-054's allowlist, D-193's second frozenset, D-192's own recorded "
        "roster) and all three are gone; a crossing that cannot be closed this "
        "cycle is a failing test and a filed concern, never a fourth."
    )


#: fallout AC-015 / AC-061 / OT-015 (D-193) — THE THREE ASSERTIONS THE
#: LAYERING RULE IS STATED BY, AND THE ONLY PLACE AN EXCEPTION MAY LIVE.
#:
#: Read from the module rather than re-typed, so a rule renamed here is a rule
#: the pin below follows instead of a pin that quietly judges nothing.
_LAYERING_ASSERTIONS = (
    test_the_three_layers_hold_with_exactly_one_named_seam,
    test_no_verifier_module_reaches_a_lifecycle_module_lazily_either,
    test_no_lifecycle_module_reaches_a_verifier_module_at_any_depth,
)


def _membership_tables_in(source: str) -> dict[str, set[str]]:
    """`{consulted name: what it is asked ABOUT}` for every `in` / `not in`.

    The value is `{"pair"}` for `(home, imported) in TABLE` and `{"name"}` for
    `imported in _LEAF_MODULES`, because those are two different kinds of
    statement and only one of them can excuse a crossing. A pair-keyed
    container answers "is THIS EDGE allowed"; a name-keyed one answers "which
    LAYER is this module in". Both shapes are collected so the rule below can
    be stated over both — a skip keyed by `f"{home} -> {imported}"` would be an
    excuse wearing the layer shape, and a walk that only knew about tuples
    would not see it.

    Stated over SOURCE rather than over a function object so the anchor below
    drives this recogniser itself. A second copy of the walk written into the
    anchor would be an anchor for a walk nothing else runs.
    """
    found: dict[str, set[str]] = {}
    for node in ast.walk(ast.parse(textwrap.dedent(source))):
        if not isinstance(node, ast.Compare):
            continue
        shape = "pair" if isinstance(node.left, ast.Tuple) else "name"
        for op, comparator in zip(node.ops, node.comparators):
            if isinstance(op, (ast.In, ast.NotIn)) and isinstance(comparator, ast.Name):
                found.setdefault(comparator.id, set()).add(shape)
    return found


def _membership_tables(func) -> dict[str, set[str]]:
    """`_membership_tables_in` over one test function's own source."""
    return _membership_tables_in(inspect.getsource(func))


def test_the_layering_rule_consults_exactly_one_exception_table():
    """fallout AC-015 / AC-061 / OT-015 (D-193, class
    `guard-allowlist-excuses-the-live-violation`) — THE MECHANISM, NOT THE
    INSTANCE.

    This class has now recurred twice on this one guard. C-054 deleted the
    layering-debt allowlist, a `(home, imported) -> reason` dict the assertions
    consulted before judging, and the guard reported `[] == []` over fourteen
    crossings. D-126 then added `_UNCLOSED_CROSS_PACKAGE_EDGES` with a careful
    argument for why an exact-equality roster was a different KIND of table, all
    three assertions skipped on it, and the guard again reported a clean tree —
    this time over the only two live crossings the tree had, while the section
    header promised "ONE named exception" and the third assertion's own
    docstring promised none at all.

    Deleting the second table is the instance fix and it is not enough: nothing
    stopped a third. So the rule is that the layering assertions may consult
    exactly ONE module-level container of EDGES, and it is the seam GI-033
    itself names. A table added to any of the three fails HERE, in the same
    commit that adds it, with the argument for it still unwritten.

    Layer sets are not exception tables and are not counted: `_LEAF_MODULES` and
    `_VERIFIER_MODULES` say which layer a module is IN, and they are asked of a
    bare name. An excuse is asked of a PAIR, which is what `_edge_exception_
    tables` keys on.

    NOR WAS THE OPEN-VIOLATIONS ROSTER, AND THE REASON IS THE ONE D-193 TURNS
    ON. While D-192's crossing was open the lifecycle rule carried a roster of
    the violations the tree still had, and this pin did not count it: a roster
    is never asked whether an edge is ALLOWED — it is COMPARED against the
    offenders the walk already found, so the crossing it records is in the
    assertion's own subject and a reader of either sees the tree is not clean. A
    skip removes an edge from the evidence; a comparison puts it in. This pin
    measures skips, which is why it must not count a comparison target:
    forbidding those would forbid the guard from stating what it found. The
    roster is gone now — D-192's edge was reversed and the lifecycle rule
    asserts `offenders == []` — so the distinction survives only as the reason
    this pin is written the way it is.
    """
    consulted: dict[str, set[str]] = {}
    for func in _LAYERING_ASSERTIONS:
        for name, shapes in _membership_tables(func).items():
            # Locals — `layered`, `lifecycle`, `names` — are how a walk asks
            # about its own working set, not how a rule grants anything. Only a
            # module-level name can be a standing table.
            if name in globals():
                consulted.setdefault(name, set()).update(shapes)

    # The emptiness guard the derivation needs: all three of the names below are
    # consulted in the shipped tree, so a walk finding nothing has gone blind
    # rather than found a rule with no exceptions. Named rather than counted,
    # per this suite's rule at `_LIFECYCLE_FLOOR`.
    assert {"_VERIFIER_TO_LIFECYCLE_SEAM", "_LEAF_MODULES", "_VERIFIER_MODULES"} <= set(
        consulted
    ), (
        f"the membership walk cannot see what the layering rule consults: "
        f"{sorted(consulted)}. The seam and the two layer sets are all asked in "
        "the shipped tree, so a result missing any of them means the scan is "
        "reading something other than these three functions and the assertions "
        "below would pass over any guard at all."
    )

    excusing = {
        name: sorted(shapes)
        for name, shapes in sorted(consulted.items())
        if "pair" in shapes and name != "_VERIFIER_TO_LIFECYCLE_SEAM"
    }
    assert excusing == {}, (
        f"layering assertion(s) consulting a second EDGE table: {excusing}. "
        "GI-033 names ONE exception — `transitions.py` dispatching the halt "
        "token into `halt.py`, one edge, one-way — and a second container asked "
        "'is this edge allowed' takes the crossing out of the assertion's "
        "subject, which is how a guard comes to report a clean tree over a tree "
        "with the coupling in it. C-054 deleted the first such table and D-193 "
        "the second. A crossing that cannot be closed this cycle is RECORDED "
        "where the walk still finds it and the assertion still names it — a "
        "roster the rule COMPARES against, never a container it skips on — and "
        "it is deleted the moment the edge is closed, which is what D-192 did "
        "to the last one."
    )

    layering = {
        name: sorted(shapes)
        for name, shapes in sorted(consulted.items())
        if name not in {"_VERIFIER_TO_LIFECYCLE_SEAM", "_LEAF_MODULES", "_VERIFIER_MODULES"}
    }
    assert layering == {}, (
        f"layering assertion(s) consulting an unrecognised module-level table: "
        f"{layering}. The rule is stated over three names and no others — the "
        "seam it excepts and the two sets that say which layer a module is in. "
        "A fourth is either a layer definition nobody declared or an excuse "
        "wearing the layer shape (`f\"{home} -> {imported}\" in TABLE` is a "
        "skip however the key is spelled), and both need saying out loud."
    )


def test_the_exception_table_recogniser_sees_a_second_table(tmp_path):
    """The anchor: the walk above finds a table when there IS one.

    A scan over three clean assertions is green whether it works or not, and the
    shape it must recognise is no longer in the tree — so the recogniser is
    driven over the exact source the three assertions carried while D-193 was
    open, plus the string-keyed spelling of the same skip, plus a layer test it
    must NOT mistake for either.
    """
    planted = tmp_path / "pretend_rule.py"
    planted.write_text(
        "def rule():\n"
        "    for home, imported in edges:\n"
        "        if (home, imported) in _VERIFIER_TO_LIFECYCLE_SEAM:\n"
        "            continue\n"
        "        if (home, imported) in _UNCLOSED_CROSS_PACKAGE_EDGES:\n"
        "            continue\n"
        "        if f'{home} -> {imported}' in _EXCUSED_ROWS:\n"
        "            continue\n"
        "        if imported in _LEAF_MODULES:\n"
        "            continue\n"
        "        offenders.append(imported)\n",
        encoding="utf-8",
    )
    found = _membership_tables_in(planted.read_text(encoding="utf-8"))

    # The pair-keyed excuse — the shape both deleted tables had.
    assert found["_UNCLOSED_CROSS_PACKAGE_EDGES"] == {"pair"}, found
    assert found["_VERIFIER_TO_LIFECYCLE_SEAM"] == {"pair"}, found
    # ...and the string-keyed one, which a tuple-only walk would have missed and
    # which the second assertion above is what catches.
    assert found["_EXCUSED_ROWS"] == {"name"}, found
    # ...while the LAYER test reads as a layer test, so the rule can forbid the
    # excuses without forbidding the sets that say which layer a module is in.
    assert found["_LEAF_MODULES"] == {"name"}, found


def test_a_planted_lazy_gate_reach_is_seen_by_the_lifecycle_walk(tmp_path):
    """The anchor: the rule above recognises the crossing it is named for.

    Once `streams.py` takes the predicate off the leaf the tree is clean, and a
    scan over a clean set is green whether it works or not. So the recogniser is
    driven over a module built to fail it — the exact shape the real edge had, a
    lifecycle door with one LAZY reach into a verifier module buried inside a
    function, which is where no module-top import scan looks.
    """
    planted = tmp_path / "pretend_door.py"
    planted.write_text(
        "from foundry_mcp.tools.foundry_state import current_cycle\n"
        "def door(fdir):\n"
        "    from foundry_mcp.tools.orchestration.gates import _blocking_defects\n"
        "    return _blocking_defects(fdir), current_cycle(fdir)\n",
        encoding="utf-8",
    )
    reached = _all_imports(planted)
    assert "gates" in reached, sorted(reached)
    assert reached & _VERIFIER_MODULES == {"gates"}, sorted(reached)
    # ...and the module-top view is what would have MISSED it, which is why the
    # three-layer rule alone could report no violations over this module.
    assert "gates" not in _module_top_imports(planted)

    # The second spelling too, since D-081 was one rule's blindness to it: the
    # walk both directions share must see `import foundry_mcp...width` as
    # plainly as it sees the `from` form.
    dotted = tmp_path / "pretend_door_dotted.py"
    dotted.write_text(
        "def door():\n"
        "    import foundry_mcp.tools.orchestration.width\n"
        "    return foundry_mcp.tools.orchestration.width\n",
        encoding="utf-8",
    )
    assert "width" in _all_imports(dotted), sorted(_all_imports(dotted))

    # fallout AC-061 / FR-063 / GI-033 / OT-015 (D-192, concern C-107) — AND THE
    # THIRD SPELLING, WHICH BOTH WALKS RESOLVED TO THE PACKAGE AND NOT THE
    # MODULE.
    #
    # `from foundry_mcp.tools import evidence` loads `tools/evidence.py` exactly
    # as the two plants above load theirs, and it read as `tools` — a name in no
    # layer — so a lifecycle door written this way was a crossing no assertion
    # in this file had anything to say about. Driven before the fix: this plant
    # gave `hits_verifier=[]` where the other two gave `['evidence']`.
    packaged = tmp_path / "pretend_door_packaged.py"
    packaged.write_text(
        "def door():\n"
        "    from foundry_mcp.tools import evidence\n"
        "    from foundry_mcp.tools.orchestration import gates\n"
        "    return evidence, gates\n",
        encoding="utf-8",
    )
    assert _all_imports(packaged) & _VERIFIER_MODULES == {"evidence", "gates"}, (
        sorted(_all_imports(packaged))
    )
    # ...and at MODULE TOP the same spelling is a real cycle edge, so the walk
    # the acyclicity check runs on must resolve it to the submodule rather than
    # to the package it was reached through.
    top = tmp_path / "pretend_module_top.py"
    top.write_text(
        "from foundry_mcp.tools.orchestration import gates\n", encoding="utf-8"
    )
    assert "gates" in _module_top_imports(top), sorted(_module_top_imports(top))

    # THE BOUNDARY, because a resolver that invents edges is a resolver somebody
    # adds an exception table to. An alias is a module when there is a module of
    # that name on disk and a SYMBOL otherwise: `DEFECT_TIERS` is a frozenset in
    # `schemas/vocab.py`, and reading it as a module would make every vocabulary
    # import in the package a phantom crossing.
    symbols = tmp_path / "pretend_symbol_import.py"
    symbols.write_text(
        "from foundry_mcp.schemas.vocab import DEFECT_TIERS\n"
        "from foundry_mcp.tools.artifacts import _load_json\n",
        encoding="utf-8",
    )
    assert _all_imports(symbols) == {"vocab", "artifacts"}, sorted(
        _all_imports(symbols)
    )


#: fallout AC-014 — SHIPPED MODULES NO TEST MODULE IMPORTS, with the reason.
#:
#: AC-014's assertion is over EVERY shipped module, not over the thirteen this
#: casting carved, and widening the window is what found this one. Same
#: shrink-only discipline as the tables above: a NEW unimported module fails
#: immediately, and an entry whose module has gained an importer ALSO fails.
#: fallout AC-014 / OT-015 (D-082) — THE TABLE IS EMPTY, AND IT IS EMPTY
#: BECAUSE THE MODULES ARE DRIVEN.
#:
#: It held `parsers/report.py` and `parsers/prove.py`, and the assertion below
#: ends `sorted(unimported) == named` — so the guard's PASSING state was a tree
#: in which both had no test module at all. Both are reached over MCP
#: (`tools/validation.py` takes `extract_last_json`, `tools/citation.py` takes
#: `Verdict` and `parse_prove_report`), and a grep of the whole `tests/` tree
#: for either module returned only the two rows themselves. Each row named an
#: honest reason, which is exactly what made the guard HOLLOW rather than
#: lenient: it passed, so nothing downstream could tell the tree had the gap.
#:
#: `tests/orchestration/test_shipped_parsers.py` drives both. The rule AC-014
#: states is now true of the tree rather than true of the tree minus a table,
#: and an entry added here would have to argue why a module nobody drives is
#: acceptable when writing the test took an hour.
_SHIPPED_WITHOUT_A_TEST_MODULE: dict[str, str] = {}


def test_every_shipped_module_is_imported_by_some_test_module():
    """fallout AC-014 / OT-015 — EVERY shipped module, not just the carved ones.

    "the boundary guard asserts every shipped module has a test module
    importing it" is a claim about the package. The assertion below it runs
    over `orchestration/*.py` — thirteen modules of the forty-three that ship —
    so a module outside that directory could carry no test at all and be
    reported as fine. This is the package-wide half.

    IMPORTED BY SOME TEST MODULE is the rule AC-014 states, and it is weaker
    than the companion rule below on purpose: GI-026's "each NEW module gets
    its own test module" is about the modules this split created, and holding
    forty-three shipped modules to a `test_<name>.py` naming convention the
    suite never adopted would be a different requirement.
    """
    pkg = Path(foundry_mcp.__file__).resolve().parent
    tests_dir = Path(__file__).resolve().parents[1]
    modules = [
        m for m in _package_source_modules() if m.name != "__init__.py"
    ]
    assert len(modules) >= 30, [str(m) for m in modules]

    # PARSED, not substring-matched: `from foundry_mcp.tools import forge_spec`
    # names the module without ever spelling its dotted path, and a scan that
    # only looked for the dotted form would report three modules as untested
    # that the suite drives directly. Both `import a.b.c` and
    # `from a.b import c` are resolved to the same dotted name.
    imported: set[str] = set()
    test_modules = sorted(tests_dir.rglob("test_*.py"))
    assert len(test_modules) >= 20, [p.name for p in test_modules]
    for path in test_modules:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{a.name}" for a in node.names)

    unimported = []
    for module in modules:
        rel = module.relative_to(pkg).as_posix()
        dotted = "foundry_mcp." + rel[:-3].replace("/", ".")
        if dotted not in imported:
            unimported.append(rel)

    named = sorted(_SHIPPED_WITHOUT_A_TEST_MODULE)
    assert sorted(unimported) == named, {
        "shipped_and_untested": sorted(set(unimported) - set(named)),
        "entry_excusing_nothing": sorted(set(named) - set(unimported)),
    }


def test_every_shipped_orchestration_module_has_a_test_module_that_imports_it():
    """fallout GI-026 / FR-005 / AC-014 / OT-015.

    "Each new module gets its own test module" is not satisfied by a module the
    suite merely touches through something else: the pin is that a file named
    for it exists AND names it, so a module carved out and never driven is
    visible on the day it lands rather than on the day it breaks.

    The COMPANION rule, which is GI-026's, and it applies to the modules this
    casting carved. `test_every_shipped_module_is_imported_by_some_test_module`
    above carries AC-014's weaker package-wide half.
    """
    here = Path(__file__).resolve().parent
    for module in _shipped_orchestration_modules():
        companion = here / f"test_{module.name}"
        assert companion.exists(), (
            f"{module.name} has no test module. Every shipped orchestration "
            f"module gets one, carved in the same casting as the source move — "
            f"expected {companion.relative_to(here.parents[2])}."
        )
        source = companion.read_text(encoding="utf-8")
        assert f"orchestration.{module.stem}" in source or f"import {module.stem}" in source, (
            f"{companion.name} exists and never names "
            f"foundry_mcp.tools.orchestration.{module.stem}, so it is a file "
            "with the right name and no subject."
        )


def test_the_orchestration_roster_is_the_shipped_package():
    """fallout AC-014 / OT-016 / GI-026 (D-183) — THE ASSERTION WHOSE ABSENCE
    LET A MODULE GO UNSEEN FOR THREE CYCLES.

    ORCHESTRATION is what every `Path(fo.__file__)` pin in this suite was
    translated into at the carve, and it was a hand-typed thirteen-tuple whose
    own comment promised it "stays the honest translation when a fourteenth is
    added". `keyfiles.py` was added in cycle 5 by the D-170 fix and the tuple
    was not, and NOTHING compared the two — so `owning_module('covers_path')`
    answered `width`, the module that merely IMPORTS the symbol, while its own
    docstring says it answers the module that DEFINES it;
    `owning_module('owning_entries')` raised for a symbol the package plainly
    defines; `orchestration_source()` silently omitted a whole file; and
    `patch_everywhere` could not reach a binding inside it, reintroducing the
    exact "a patch reaches some callers and not others" hazard it was written
    to close.

    TWO DERIVATIONS, PINNED EQUAL, AND NEITHER TYPED.
    `tests/orchestration/_env.py` imports the package directory;
    `_shipped_orchestration_modules` globs the same
    directory from the leaf's own location. They are separate walks reaching
    the same tree, which is what makes this a comparison rather than a
    tautology — and either one going blind fails here instead of somewhere a
    reader would have to already suspect.
    """
    roster = {Path(m.__file__).resolve() for m in ORCHESTRATION}
    shipped = {p.resolve() for p in _shipped_orchestration_modules()}
    assert roster == shipped, {
        "in_the_roster_only": sorted(p.name for p in roster - shipped),
        "shipped_but_unrostered": sorted(p.name for p in shipped - roster),
    }
    # The anchor: a scan over a set that agreed with itself while both were
    # empty would be green and worthless, and `keyfiles.py` is named because it
    # is the module the hand-typed roster missed.
    assert len(roster) >= 14, sorted(p.name for p in roster)
    assert "keyfiles.py" in {p.name for p in roster}, sorted(p.name for p in roster)

    # ...and the consequence the staleness actually had, driven rather than
    # asserted about the roster: the two symbols `keyfiles.py` defines and
    # `width.py` merely imports now resolve to the module that DEFINES them.
    assert owning_module("covers_path") is _keyfiles, owning_module("covers_path")
    assert owning_module("owning_entries") is _keyfiles, owning_module("owning_entries")
    assert "covers_path" in vars(_width), "the drive no longer crosses an importer"


def test_the_package_marker_re_exports_nothing():
    """fallout FR-004 / GI-010 / AC-013 / OT-012 — NO FACADE, and this is where
    that is either true or not.

    A package `__init__` that re-exported the monolith's symbols would BE the
    monolith under a new name: every importer would keep the spelling it had,
    this guard would see one node where there are thirteen, and the split would
    have moved code without moving any coupling.
    """
    marker = _orchestration_dir() / "__init__.py"
    tree = ast.parse(marker.read_text(encoding="utf-8"))
    statements = [
        n for n in tree.body
        if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))
    ]
    assert statements == [], (
        "the orchestration package marker carries code. It re-exports nothing "
        "by design; a name bound here is a facade, however small."
    )

    # ...and nothing anywhere still names the module that was split.
    #
    # fallout OT-012 / GI-010 / AC-013 (D-198) — THE WALK READ ONE SPELLING OF
    # THREE, AND THE REMEDY WAS ALREADY IN THIS FILE.
    #
    # This built its offender set from `ImportFrom.module` alone. So
    # `from foundry_mcp.tools.foundry_orchestrator import x` was caught, while
    # `from foundry_mcp.tools import foundry_orchestrator` resolved to `tools`
    # and `import foundry_mcp.tools.foundry_orchestrator` was not an
    # `ImportFrom` at all — two of the three ways Python spells the same load,
    # both invisible. DRIVEN: a planted `tools/foundry_orchestrator.py` with a
    # SHIPPED module reaching it in the second spelling left this test green,
    # so the assertion whose message reads "shipped module(s) still importing
    # foundry_orchestrator" did not fire on a shipped module importing
    # foundry_orchestrator.
    #
    # That is D-081's class in its third spelling and D-192's in its second,
    # and `_all_imports` was written for exactly this and adopted by the two
    # layering walks a thousand lines above while this one kept its own
    # comprehension. It is not a new predicate here; it is the one that already
    # existed, finally called. `also_by_name` is what lets it answer about a
    # module whose absence is the requirement — see its docstring.
    plugin_root = Path(artifacts.__file__).resolve().parents[4]
    offenders = [
        str(p.relative_to(plugin_root))
        for p in sorted(plugin_root.rglob("*.py"))
        if _INSTALLED_DEPENDENCY_DIRS.isdisjoint(p.parts)
        and "foundry_orchestrator" in _all_imports(
            p, also_by_name=("foundry_orchestrator",)
        )
    ]
    # THE WHOLE TREE IS HELD ABSOLUTELY NOW, shipped source and tests alike.
    #
    # A src/ module importing a module that does not exist is an ImportError at
    # server startup — zero or the server does not run — and the ten sibling
    # test modules that were in flight behind an inventory have all repointed.
    # The two halves are still reported separately, because they fail for
    # different reasons and a reader wants to know which: a shipped offender
    # breaks the server, a test offender breaks a suite.
    shipped = [o for o in offenders if "/src/" in o]
    assert shipped == [], (
        f"shipped module(s) still importing foundry_orchestrator: {shipped}. It "
        "does not exist; each needs the module that defines the symbol."
    )
    outstanding = sorted(o for o in offenders if "/src/" not in o)
    assert outstanding == [], {
        "test module(s) still importing foundry_orchestrator": outstanding,
        "what to do": (
            "the module does not exist; each offender needs the module that "
            "now defines the symbol, from casting 2's symbol map. There is no "
            "roster to record it in — see the comment above this test."
        ),
    }


def test_the_no_facade_scan_sees_all_three_spellings_of_the_deleted_monolith(tmp_path):
    """fallout OT-012 / GI-010 / AC-013 / FR-004 (D-198) — THE ANCHOR FOR THE
    WALK ABOVE, WHICH IS GREEN OVER A CLEAN TREE WHETHER IT WORKS OR NOT.

    The tree has no `foundry_orchestrator` importer and must not gain one, so
    the assertion above passes on an empty offender list — and passed on an
    empty offender list while two of the three spellings were invisible to it.
    That is what makes the recogniser worth driving separately: this is the
    test that fails if the scan goes blind again, on the day it goes blind
    rather than on the day somebody reintroduces the facade.

    ONE PLANT PER SPELLING, all three naming the same load. The second is the
    one `_submodules_named_by` cannot resolve here and `also_by_name` can:
    `tools/foundry_orchestrator.py` is DELETED, so there is no file on disk for
    an alias to resolve against, which is precisely the state OT-012 requires
    and precisely the state that made disk resolution the wrong reading.
    """
    named = ("foundry_orchestrator",)

    dotted_module = tmp_path / "spelling_one.py"
    dotted_module.write_text(
        "from foundry_mcp.tools.foundry_orchestrator import _phase_transition\n",
        encoding="utf-8",
    )
    assert "foundry_orchestrator" in _all_imports(dotted_module, also_by_name=named)

    from_package = tmp_path / "spelling_two.py"
    from_package.write_text(
        "from foundry_mcp.tools import foundry_orchestrator\n"
        "def door(fdir):\n"
        "    return foundry_orchestrator._phase_transition(fdir)\n",
        encoding="utf-8",
    )
    assert "foundry_orchestrator" in _all_imports(from_package, also_by_name=named), (
        sorted(_all_imports(from_package, also_by_name=named))
    )
    # ...and this is the spelling the walk was blind to, stated as the delta
    # rather than asserted about in prose: WITHOUT the caller-named reading the
    # same plant resolves to `tools` and matches nothing.
    assert "foundry_orchestrator" not in _all_imports(from_package)

    plain_import = tmp_path / "spelling_three.py"
    plain_import.write_text(
        "def door():\n"
        "    import foundry_mcp.tools.foundry_orchestrator\n"
        "    return foundry_mcp.tools.foundry_orchestrator\n",
        encoding="utf-8",
    )
    assert "foundry_orchestrator" in _all_imports(plain_import, also_by_name=named)

    # THE BOUNDARY the reading above does not cross. `also_by_name` names ONE
    # module; every other alias is still resolved on disk, so a symbol import
    # is a symbol import and the phantom-crossing hazard `_submodules_named_by`
    # was written against is not reopened by this caller.
    symbols = tmp_path / "spelling_boundary.py"
    symbols.write_text(
        "from foundry_mcp.schemas.vocab import DEFECT_TIERS\n"
        "from foundry_mcp.tools.artifacts import _load_json\n",
        encoding="utf-8",
    )
    assert _all_imports(symbols, also_by_name=named) == {"vocab", "artifacts"}, (
        sorted(_all_imports(symbols, also_by_name=named))
    )

    # ...and the default is untouched, which is what makes this additive for
    # the two layering walks that call the same helper.
    assert _all_imports(dotted_module) == {"foundry_orchestrator"}




#: fallout FR-043 / GI-033 — THE ORCHESTRATOR-TO-SPAWN CYCLE, AS EDGES.
#:
#: FR-043 requires the two lazy-import cycles to be "preserved or removed
#: deliberately". The orchestrator-to-report one is genuinely removed. This one
#: is preserved, and preserved means every edge of it is a NAMED lazy seam that
#: does not exist at import time — not that the back edges happen to be lazy
#: while the forward ones are module-top.
# fallout GI-033 / AC-061 / FR-063 (D-080) — THREE MEMBERS LEFT THIS ROSTER
# WITH THEIR SEAMS, AND ONE REMAINS.
#
# `teams`, `width` and `transitions` each reached `foundry_spawn` lazily for
# `_manifest_shape_problem` or `_skipped_stream_ids` — predicates BOTH layers
# read, two of them from the verifier side, which GI-033 refuses outright and
# which no module-top import scan could see because every reach was lazy. Both
# are leaf symbols now (`artifacts.manifest_shape_problem`,
# `foundry_state.skipped_stream_ids`), reached at module top, so the coupling
# is gone rather than deferred and the roster shrinks with it.
#
# `spend` stays, and it is a different shape: `_agent_id_for_casting` is a
# spawn-time identity that only the lifecycle layer asks about, so the seam is
# lifecycle-to-lifecycle and the laziness answers an import cycle rather than a
# layering rule.
_SPAWN_SEAM_MODULES = ("spend",)


def test_the_orchestrator_to_spawn_cycle_is_lazy_in_both_directions():
    """fallout FR-043 / GI-033 / AC-061 — D-043.

    `foundry_spawn.py` imported `orchestration.teams` and `orchestration.width`
    at MODULE TOP while four orchestration modules reached back into it lazily,
    so the cycle was live in one direction and documented in neither — and the
    `width` edge was additionally a lifecycle module importing a VERIFIER, which
    GI-033 refuses outright.

    Both halves are asserted, because either alone is satisfiable by the wrong
    tree: a scan of the forward edges alone passes on a module that reaches
    nothing, and a scan of the back edges alone passes on the shape the defect
    was filed on.
    """
    spawn = Path(foundry_mcp.__file__).resolve().parent / "tools" / "foundry_spawn.py"

    # fallout GI-033 / FR-043 (D-198's class, swept) — ALL FOUR SCANS IN THIS
    # TEST READ ONE SPELLING OF THREE, AND TWO OF THEM READ A DIFFERENT ONE.
    #
    # Driven, on a plant per spelling: this set saw
    # `from foundry_mcp.tools.orchestration.teams import agent_model` and
    # `from foundry_mcp.tools.orchestration import teams` — the latter only by
    # accident, because the prefix test carries no trailing dot — and was
    # INVISIBLE to `import foundry_mcp.tools.orchestration.teams`, a module-top
    # edge that closes this cycle at import time exactly as the other two do.
    # The accident cut the other way too: the second spelling resolved to
    # `foundry_mcp.tools.orchestration`, whose last segment is `orchestration`
    # and is in no layer, so `from foundry_mcp.tools.orchestration import
    # keyfiles` — a LEAF, allowed by the narrowing below — would have been
    # judged non-leaf and failed. One walk read two spellings as three
    # different things.
    #
    # `_module_top_imports` is the resolution and it already existed: it walks
    # `tree.body` for all three spellings through `_submodules_named_by` and
    # returns BASENAMES, filtered on the trailing dot so the package itself is
    # not mistaken for a submodule of itself. The leaf narrowing then compares
    # like with like.
    module_top = _module_top_imports(spawn)
    # fallout FR-009 / GI-033 (D-170, casting 7's concern C-079) — A LEAF IS
    # NOT AN EDGE OF THIS CYCLE, AND THE NARROWING IS STATED RATHER THAN
    # ASSUMED.
    # ----------------------------------------------------------------------
    # This asserted `module_top == set()`, which is the right assertion for the
    # thing it was filed on — `teams` and `width` at module top, one of them a
    # VERIFIER — and one rung too broad for a LEAF. `orchestration/keyfiles.py`
    # imports nothing at all (`test_keyfiles.py` asserts that on its source) and
    # `orchestration/__init__.py` re-exports nothing, so
    # `foundry_spawn -> orchestration.keyfiles` reaches no module that can reach
    # back: there is no cycle for laziness to defer, and deferring it anyway
    # would be a seam written for a hazard that is not there.
    #
    # THE ASSERTION IS NARROWED, NOT WEAKENED. The subject is the same set of
    # imports; what changed is that a LEAF member is judged separately from the
    # rest, and the rest must still be EMPTY. Every module this test was filed
    # on is a non-leaf and still fails, and a leaf that stops being one fails
    # here the same day `_LEAF_MODULES`' own property assertion fails.
    non_leaf = {m for m in module_top if m not in _LEAF_MODULES}
    assert non_leaf == set(), (
        f"foundry_spawn.py imports {sorted(non_leaf)} at module top. Every "
        "edge of this cycle is a lazy seam; a module-top one closes the cycle "
        "at import time and, for a verifier module, breaks GI-033 outright."
    )

    # ...and it DOES reach them, lazily, or the assertion above is vacuous.
    # `_all_imports` is the any-depth half of the same scanner, so the two
    # halves of this test cannot come to disagree about what an import is.
    lazy = _all_imports(spawn) & {p.stem for p in _shipped_orchestration_modules()}
    assert lazy, "foundry_spawn.py reaches no orchestration module at all"

    # The back edges are lazy too, and every one of them is inside a function.
    # The back edges are read through the same two halves of the one scanner,
    # for the reason the forward edges are: driven, `endswith("foundry_spawn")`
    # over `ImportFrom.module` saw only the dotted form, so
    # `from foundry_mcp.tools import foundry_spawn` and
    # `import foundry_mcp.tools.foundry_spawn` were both module-top edges this
    # assertion could not see.
    for name in _SPAWN_SEAM_MODULES:
        path = _orchestration_dir() / f"{name}.py"
        top = {
            m.rsplit(".", 1)[-1] for m in _module_top_dotted_imports(path)
        } & {"foundry_spawn"}
        assert top == set(), (
            f"{name}.py imports foundry_spawn at module top, which closes the "
            "cycle this seam set exists to keep open"
        )
        anywhere = _all_imports(path) & {"foundry_spawn"}
        assert anywhere, (
            f"{name}.py is named as a spawn seam and reaches foundry_spawn "
            "nowhere; the seam roster has outlived the coupling it records"
        )

    # The cycle is WRITTEN DOWN. FR-043's own sentence says the seams are "kept
    # in teams.py and guidance.py"; guidance.py holds no reference to spawn and
    # needs none, and a resolution that does not describe the tree is the half
    # of this defect that no import scan can catch.
    source = spawn.read_text(encoding="utf-8")
    assert "FR-043" in source, "the seam carries no citation of the rule it keeps"
    for name in _SPAWN_SEAM_MODULES:
        assert f"`{name}.py`" in source, (
            f"the seam comment does not name {name}.py as one of the back edges"
        )




# --------------------------------------------------------------------------- #
# fallout NFR-011 (D-150) — every requirement id a shipped module's prose cites
# resolves to a row in THIS run's spec.
# --------------------------------------------------------------------------- #

#: The two id families this pin resolves, and where each is declared.
#:
#: Requirement ids are declared in the spec three ways — `**FR-007**` in a bullet
#: list, `### US-001:` as a heading, and `| GI-033 |` as a table's first cell —
#: and all three are harvested, because a pin that knew only one spelling would
#: report two thirds of a correct spec as unresolvable.
_SPEC_ID_DECLARATIONS = (
    r"\*\*((?:US|FR|NFR|AC|GI|CT|ST|OT)-\d+)\*\*",
    r"^#+\s*((?:US|FR|NFR|AC|GI|CT|ST|OT)-\d+)",
    r"^\|\s*((?:GI|CT|ST|OT)-\d+)\s*\|",
)


#: This run's spec, relative to the repo root, spelled ONCE.
#:
#: The skip below names it, and an ABSOLUTE path in a skip reason is not a
#: stable thing to name. The gate re-executes every evidence command inside a
#: detached worktree, and `worktree_helpers.py#_claim_worktree_path` suffixes
#: that worktree's directory name when a peer already holds the claim
#: (`sweep-evidence` becomes `sweep-evidence-1`). Interpolating the resolved
#: path therefore wrote the checkout's location into the body of every
#: committed evidence log that reports this skip, and those logs then failed
#: to reproduce on the suffix alone whenever two sweeps overlapped — a
#: byte-mismatch about nothing, in logs whose commands were entirely correct.
#: A repo-relative name is identical in every checkout and still says which
#: file is missing, which the bare wording its sibling skip uses does not.
_RUN_SPEC_RELATIVE = "forge-specs/foundry-run-fallout/spec.md"


def _run_spec_path() -> Path:
    """This run's spec.

    Derived from the plugin root the way `shipped_python_files` derives it — up
    to the directory ASSERTED to be `foundry`, then out of `plugins/` — rather
    than by counting `parents[N]` from this file, which is the count that goes
    wrong silently when a directory is added and turns this pin into a skip.
    """
    plugin_root = Path(artifacts.__file__).resolve().parents[4]
    assert plugin_root.name == "foundry", plugin_root
    return plugin_root.parents[1] / _RUN_SPEC_RELATIVE


def test_every_requirement_id_the_orchestration_prose_cites_exists():
    """fallout NFR-011 (D-150) — an attribution that resolves to nothing.

    NFR-011: "Every prose rule this effort states is DERIVED FROM or pinned to a
    code constant." Docstring headers in the shipped modules attributed their
    rules to identifiers carried over from the PREDECESSOR run's spec, which
    name unrelated rows in this one — `halt.py#_halt_if_capped` headed "ST-008 /
    CT-016" where ST-008 is the stream-record transition and CT-016 is
    `scripts/measure-run.py`; `gates.py#_halted_refusal` put quoted sentences in
    the mouths of FR-024 (rosters here) and FR-052 (Foundry-Validate-Castings
    here) — and `halt.py#_seal_halted` cited `A-048`, which DOES NOT EXIST: this
    run's transcript ends at A-047, so that attribution resolved to nothing at
    all.

    THIS PIN CATCHES THE RESOLVES-TO-NOTHING HALF, which is the half a
    mechanism can decide. "Cites a row that exists and means something else" is
    a judgement, and the `fallout ` qualifier convention
    (`tests/test_spec_id_convention.py`) is what carries that half; between them
    a maintainer resolving a cite lands on a real row of the right spec.

    DERIVED FROM THE SPEC ITSELF, never a hand list, so a row the spec gains is
    citable the day it lands and a row it loses fails the cites that named it.
    """
    spec = _run_spec_path()
    if not spec.exists():
        pytest.skip(f"this checkout carries no {_RUN_SPEC_RELATIVE}")
    text = spec.read_text(encoding="utf-8")

    declared: set[str] = set()
    for pattern in _SPEC_ID_DECLARATIONS:
        declared |= set(re.findall(pattern, text, re.M))
    assert len(declared) > 100, (
        f"only {len(declared)} requirement id(s) harvested from {spec}; the "
        "declaration patterns have gone blind and the assertion below would "
        "report every cite in the package as unresolvable"
    )
    answers = set(re.findall(r"\bA-\d{3}\b", text))
    assert answers, "no transcript answer ids in the spec; the harvest is blind"

    unresolvable: dict[str, list[str]] = {}
    cited = 0
    for module in _shipped_orchestration_modules():
        source = module.read_text(encoding="utf-8")
        for match in re.finditer(
            r"\b((?:US|FR|NFR|AC|GI|CT|ST|OT)-\d+|A-\d{3})\b", source
        ):
            name = match.group(1)
            cited += 1
            known = answers if name.startswith("A-") else declared
            if name not in known:
                unresolvable.setdefault(name, []).append(module.name)
    assert cited > 200, (
        f"only {cited} requirement cite(s) seen across the package; the scan is "
        "blind and this assertion would pass over any tree"
    )
    assert unresolvable == {}, (
        f"requirement id(s) cited by shipped prose that this run's spec does "
        f"not declare: { {k: sorted(set(v)) for k, v in unresolvable.items()} }. "
        "An attribution a maintainer cannot resolve sends them to an unrelated "
        "requirement or to nothing at all; re-attribute the comment to the row "
        "that actually states the rule."
    )


def test_the_citation_pin_recognises_an_id_the_spec_does_not_declare(tmp_path):
    """The anchor: the harvest above rejects a cite that resolves to nothing.

    A scan over a clean package is green whether it works or not, so the
    recogniser is driven over the exact string D-150 was filed on — `A-048`,
    one past the last answer this run's transcript carries.
    """
    spec = _run_spec_path()
    if not spec.exists():
        pytest.skip("this checkout carries no run spec")
    answers = set(re.findall(r"\bA-\d{3}\b", spec.read_text(encoding="utf-8")))
    assert "A-047" in answers, sorted(answers)[-3:]
    assert "A-048" not in answers, "the transcript grew; re-check D-150's example"




def test_the_verifier_walks_now_have_evidence_py_in_their_subject_set():
    """fallout AC-061 / FR-063 (D-126, concern C-067) — the SUBJECT SET is the fix.

    The three-layer walk and its any-depth companion both iterated
    `_shipped_orchestration_modules()`, so `tools/evidence.py` — a verifier
    module by the spec's dependency flow AND by `vocab.VERIFIER_PATH_PATTERNS` —
    had its outgoing edges judged by nothing. DRIVEN before the fix: a module-top
    `import foundry_mcp.tools.display` planted at the top of evidence.py in an
    isolated copy left all three layering tests PASSING, and a lazy
    `evidence -> report_seal` plant was equally invisible, while the same plant
    naming `orchestration.gates` failed correctly.

    So the walk was never broken and a recogniser anchor would not have found
    this. What is asserted here is the thing that WAS wrong: that the set the
    walks iterate contains the module, and that the guard and the width rule now
    agree about which files are the verifier layer.
    """
    layered = _layered_modules()
    assert "evidence" in layered, sorted(layered)
    assert "evidence" in _VERIFIER_MODULES
    # It is NOT in the orchestration package, which is exactly why the old
    # subject set could not see it.
    assert "evidence" not in {p.stem for p in _shipped_orchestration_modules()}

    # The guard and `vocab.VERIFIER_PATH_PATTERNS` agree: every module this set
    # names is one the width rule also calls a verifier, so a diff touching it
    # forces FULL and the layering rule judges it. Disagreement between the two
    # is what C-067 reported.
    tools_rel = "plugins/foundry/mcp-server/src/foundry_mcp/tools"
    for name in sorted(_VERIFIER_MODULES):
        path = layered[name]
        relative = (
            f"{tools_rel}/orchestration/{name}.py"
            if path.parent.name == "orchestration"
            else f"{tools_rel}/{name}.py"
        )
        assert vocab.is_verifier_path(relative, "spec.md"), (name, relative)

    # ...and the converse half of AC-061: `halt.py` is a verifier by neither.
    assert "halt" not in _VERIFIER_MODULES
    assert not vocab.is_verifier_path(f"{tools_rel}/orchestration/halt.py", "spec.md")




def test_a_second_definition_in_measure_run_is_refused(tmp_path):
    """fallout AC-011 / OT-011 / GI-024 (D-131 / D-132) — the anchor, on the file
    the requirement names.

    A scan over a clean window is green whether it looks at the right files or
    not, which is exactly how this went unnoticed: the rule held, so the guard
    passed, and nobody could tell it was passing over a set that excluded
    `scripts/measure-run.py`. The recogniser is therefore driven over the EXACT
    plant D-131 was filed on — `spend_rollup` and `markdown_sections` defined at
    top level in that script, shadowing the module-scope imports it already
    makes — without touching the real file.

    The window helper is asserted to CONTAIN the script first, so a future
    refactor that quietly drops it fails here rather than in six months.
    """
    window = _consolidation_scan_modules()
    names = [p.name for p in window]
    assert "measure-run.py" in names, names[-5:]
    assert len(window) == len(_package_source_modules()) + len(_CONSOLIDATION_SCRIPTS)

    # fallout AC-011 / OT-011 (D-178) — `inspect_mode_rows` JOINS THE PLANT.
    #
    # D-131's plant was `spend_rollup` and `markdown_sections`, and both were in
    # the named inventory, so the recogniser was driven only over rows it
    # already had. `inspect_mode_rows` is the third: AC-011 names it by concern
    # ("inspect-mode rows"), `measure-run.py` imports it at module scope, and it
    # was in NEITHER guard — not in the typed list, and not in the package sweep
    # whose window excludes this file. Driving it here is what makes this anchor
    # cover the row that was missing rather than only the rows that were there.
    plants = {"spend_rollup", "markdown_sections", "inspect_mode_rows"}

    # The real file defines none of them today — the one implementation exists,
    # which is why only the guard needed fixing.
    real = next(p for p in window if p.name == "measure-run.py")
    assert plants.isdisjoint(
        _top_level_definitions(real)
    ), "measure-run.py has grown a second definition; that is the real finding"

    # The plant, judged by the same walk the test above uses.
    planted = tmp_path / "measure-run.py"
    planted.write_text(
        real.read_text(encoding="utf-8")
        + "".join(f"\n\ndef {name}(*a, **k):\n    return {{}}\n" for name in sorted(plants)),
        encoding="utf-8",
    )
    defined = _top_level_definitions(planted)
    assert plants <= defined, sorted(defined)[:8]

    # ...and each name really is one the leaf already owns, so the plant is a
    # SECOND definition rather than a first.
    leaf = Path(artifacts.__file__).resolve().parent / "foundry_state.py"
    owned = _top_level_definitions(leaf)
    assert plants <= owned, sorted(plants - owned)

    # ...and the two the derivation covers really are names the script IMPORTS
    # at module scope, which is what makes a top-level def of either a SHADOWING
    # second definition rather than an unrelated helper sharing a spelling.
    # `markdown_sections` is deliberately outside that set: the script does not
    # name it at all, so it is a second definition the TYPED inventory catches
    # and the derivation cannot — which is why AC-011's five stay typed rather
    # than being replaced by the import walk.
    imported, _module_bound = _leaf_symbols_the_consolidation_scripts_import()
    assert {"spend_rollup", "inspect_mode_rows"} <= imported, sorted(imported)
    assert "markdown_sections" not in imported
    assert "markdown_sections" in _CONSOLIDATED_HELPERS


# --------------------------------------------------------------------------- #
# fallout AC-015 / FR-006 / GI-025 / OT-015 — THE SPELLING CLASS, AND WHY THE
# GUARD THAT RECOGNISED IT WAS RETIRED RATHER THAN REPAIRED A FOURTH TIME.
#
# A Python import has three spellings of the same load:
#
#     from foundry_mcp.tools.evidence import verify   # node.module is the MODULE
#     from foundry_mcp.tools import evidence          # node.module is the PARENT
#     import foundry_mcp.tools.evidence               # not an ImportFrom at all
#
# A reading written against one of them walks past the other two. This run
# found and fixed that blindness six times: the two layering walks (D-192,
# concern C-107), the no-facade whole-tree walk, this file's one-way seam scan
# and the four `foundry_spawn` cycle scans (D-198), the arming condition in
# `tests/test_protocol_prose.py` (C-115), and the ledger-door roster in
# `tests/test_observations.py` (C-116) — where a planted real defect went from
# RED to GREEN and reopened D-127.
#
# A RECOGNISER FOR THE CLASS STOOD HERE FOR THREE CYCLES. It scanned every
# lexical scope in the plugin that tested for an `ast.ImportFrom` and judged
# whether that scope's `.module` reading could lose a spelling. It retrodicts
# all six as isolated plants; three of the six it found in the tree itself. It
# never caught a seventh, and it filed seven defects against itself: D-199,
# D-200 and D-201, then D-203, D-204, D-205 and D-206 against the repairs of
# those.
#
# IT WAS RETIRED ON A MEASUREMENT AND NOT ON FATIGUE. C-116's blind reading and
# C-116's own FIX are the same AST shape:
#
#     node.module == "foundry_mcp.tools.foundry"   # the defect
#     node.module == package                       # the fix's spelling-two arm
#
# What separates them is what the compared value MEANS and what the sibling
# arms DO — semantics, not syntax. Every predicate over that shape therefore
# flags both or neither, and the recogniser's whole history is that
# oscillation. Where it chose NEITHER: the exemption was keyed on the UNPARSED
# TEXT of the receiver, and all 7 of its 24 exempted scopes spelled that text
# `node.module`, so one routed arm silently exempted the hand-rolled arm beside
# it (D-203); and its `ast.Import` condition asked only that the scope MENTION
# the type, which `elif isinstance(node, ast.Import): pass` satisfies (D-204).
# Where it chose BOTH: a sound reader was an offender for prefixing a package
# outside `foundry_mcp` (D-206), and `_ast` — from which `ImportFrom` is
# re-exported — broke it in both directions at once, a blind scope going
# invisible behind `from _ast import ImportFrom as IF` while a sound one was
# accused for `from _ast import Import as PlainImport` (D-205).
#
# Driven before retiring, so this is a measurement and not a forecast: the one
# available repair that is EXACT rather than a further proxy — keying the
# exemption on AST node identity instead of on unparsed text — reports four
# findings over today's tree, and all four are sound sites, `test_observations
# .py`'s own three-spelling fix among them. A guard whose exact form accuses
# the repair it exists to reward is not one repair away from working.
#
# WHAT DID NOT GO, AND IS THE WHOLE OF THE ANSWER. Detecting this class is
# undecidable by syntax. The REMEDY is not, and it is asserted:
# `_submodules_named_by` resolves an alias on disk and answers for all three
# spellings, `_all_imports` is the walk built on it, both carry the class in
# their own docstrings where an author writing an import walk is standing, and
# `test_the_no_facade_scan_sees_all_three_spellings_of_the_deleted_monolith`
# drives that claim with one plant per spelling. Route a new import reading
# through them. That is what all six fixes reduced to, and it is the thing the
# scan was never needed in order to say.
#
# DO NOT RE-CREATE THE SCAN WITHOUT FIRST ANSWERING THE PARAGRAPH ABOVE.
# Narrower is where D-203 and D-204 came from and wider is where D-205 and
# D-206 came from, so neither direction is the open question. The open question
# is how a reading's soundness is decided at all when the defect and its fix
# share a shape. Until that has an answer, a scan here spends attention and
# returns none.
# --------------------------------------------------------------------------- #


def test_the_consolidation_scan_reads_all_three_spellings_of_the_leaf(tmp_path):
    """fallout AC-011 / GI-024 / OT-015 — the narrowness above, driven.

    `_leaf_symbols_the_consolidation_scripts_import` reads ONE spelling into its
    bare-name set on purpose, because one spelling is all that binds a bare
    name. The other two are resolved and returned, and this is where that stops
    being a claim: a plant in each spelling, judged against the real script.
    """
    real = next(
        p for p in _consolidation_scan_modules()
        if p.name in _CONSOLIDATION_SCRIPTS
    )
    body = real.read_text(encoding="utf-8")
    package, _, stem = _CONSOLIDATION_LEAF.rpartition(".")

    def drive(header: str) -> tuple[set[str], set[str]]:
        planted = tmp_path / real.name
        planted.write_text(header + "\n" + body, encoding="utf-8")
        original = _consolidation_scan_modules
        try:
            globals()["_consolidation_scan_modules"] = lambda: [planted]
            return _leaf_symbols_the_consolidation_scripts_import()
        finally:
            globals()["_consolidation_scan_modules"] = original

    # SPELLING ONE binds bare names — the shadowing hazard the pin is for.
    names, modules = drive(f"from {_CONSOLIDATION_LEAF} import now_iso as _probe")
    assert "now_iso" in names, sorted(names)[:8]
    assert modules == set(), modules

    # SPELLING TWO binds the MODULE. Nothing joins the bare-name set, and the
    # binding is REPORTED rather than silently dropped, which is the whole
    # difference between reading one spelling and seeing one.
    names, modules = drive(f"from {package} import {stem} as _probe_module")
    assert "_probe_module" in modules, modules
    assert "_probe_module" not in names

    # SPELLING THREE binds the ROOT package; same reasoning, same answer.
    names, modules = drive(f"import {_CONSOLIDATION_LEAF}")
    assert _CONSOLIDATION_LEAF.split(".")[0] in modules, modules

    # ...and the real script, unplanted, still carries the floor the pin needs.
    assert len(_leaf_symbols_the_consolidation_scripts_import()[0]) >= 15
