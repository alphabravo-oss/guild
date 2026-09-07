"""What a `key_files` entry is — the one statement, and the four sites reading it.

fallout FR-009 (D-170, casting 7's concern C-079) / fallout GI-026 /
fallout FR-005 / fallout AC-014: each new module gets its own test module, landed in the same casting as the
source move.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

from foundry_mcp.tools.orchestration import directives as _directives
from foundry_mcp.tools.orchestration import keyfiles as _keyfiles
from foundry_mcp.tools.orchestration import transitions as _transitions
from foundry_mcp.tools.orchestration import width as _width
from foundry_mcp.tools.orchestration.keyfiles import (
    DIRECTORY_ENTRY_SUFFIX,
    covers_path,
    manifest_spelling,
    owning_entries,
)

from tests.orchestration._env import (  # noqa: F401
    _write_state,
    run_env,
)


def test_a_directory_entry_covers_what_is_beneath_it_and_nothing_beside_it():
    """fallout FR-009 (D-170) — the reading, and the near-miss it must refuse.

    The trailing slash is part of the comparison, which is what makes the prefix
    a SEGMENT boundary rather than a string prefix. A predicate that stripped it
    before comparing would answer yes for a sibling directory whose name merely
    starts the same way, and would then hand a lead a casting that owns nothing
    of the sort.
    """
    entry = "plugins/foundry/mcp-server/src/foundry_mcp/tools/orchestration/"
    assert covers_path(entry, entry + "streams.py") is True
    assert covers_path(entry, entry + "sub/deeper.py") is True
    # The near-miss: a sibling directory sharing a prefix but not a segment.
    assert covers_path(entry, entry.rstrip("/") + "XX/a.py") is False
    # The directory itself, spelled without its slash, is not a path beneath it.
    assert covers_path(entry, entry.rstrip("/")) is False


def test_a_file_entry_is_answered_exactly_as_equality_always_answered_it():
    """fallout FR-009 (D-170) — the fix WIDENS what matches and changes nothing
    that already did.

    A manifest with no directory entry must be answered exactly as it was before
    this module existed, or the fix for an under-match becomes an over-match —
    which is the worse direction for a dispatcher, because it hands work to a
    casting that does not own it and nothing refuses.
    """
    assert covers_path("src/one.py", "src/one.py") is True
    assert covers_path("src/one.py", "src/one.pyc") is False
    assert covers_path("src/one.py", "src/two.py") is False
    assert covers_path("src/one.py", "other/src/one.py") is False


def test_an_unfilled_entry_claims_nothing_rather_than_everything():
    """fallout FR-009 (D-170) — the failure mode a bare `startswith("")` has.

    An empty or whitespace-only `key_files` cell is a manifest nobody finished,
    and the honest answer for it is "owns nothing". Answering "owns everything"
    would make one blank cell resolve every file in the repo to that casting,
    which is the same silent mis-dispatch D-170 measured with the sign flipped.
    """
    for blank in ("", "   ", "\n", None):
        assert covers_path(blank, "src/one.py") is False
    # ...and a path that normalises away is not covered by anything either.
    assert covers_path("src/", "") is False
    assert covers_path("src/", "   ") is False


def test_both_spellings_of_one_path_compare_equal():
    """fallout FR-009 (D-170) — the manifest is hand-written and the diff is not.

    `git diff --name-only` emits forward-slashed repo-relative paths; a manifest
    carries whatever its author typed, which on this run has included a leading
    `./`. A comparison that trusts the two to agree is a comparison that misses,
    which is this module's own class one character over.
    """
    assert covers_path("src/pkg/", "./src/pkg/one.py") is True
    assert covers_path("./src/pkg/", "src/pkg/one.py") is True
    assert covers_path("  src/one.py  ", "src/one.py") is True
    assert manifest_spelling("./a/b.py") == "a/b.py"
    assert manifest_spelling("a\\b.py") == "a/b.py"
    # The trailing slash SURVIVES normalisation — it is the whole of what the
    # reading turns on, so a normaliser that tidied it away would silently turn
    # every directory entry into a file entry.
    assert manifest_spelling("./a/b/").endswith(DIRECTORY_ENTRY_SUFFIX)


def test_owning_entries_names_the_entry_a_covered_path_came_through():
    """fallout FR-009 (D-170) — a covered file is in nobody's `key_files`
    literally, so a message naming only the file sends a lead looking for a line
    that is not there.
    """
    entries = ["tools/orchestration/", "server.py"]
    assert owning_entries(entries, ["tools/orchestration/streams.py"]) == [
        "tools/orchestration/"
    ]
    assert owning_entries(entries, ["server.py"]) == ["server.py"]
    assert owning_entries(entries, ["tools/other.py"]) == []
    # Order is the manifest's, and an entry is named once however many paths it
    # covers.
    assert owning_entries(entries, [
        "tools/orchestration/a.py", "tools/orchestration/b.py", "server.py",
    ]) == ["tools/orchestration/", "server.py"]
    # A non-list, or a list with non-string cells, answers rather than raises:
    # this is read from a hand-written manifest.
    assert owning_entries("nope", ["a.py"]) == []
    assert owning_entries([None, 3, "a.py"], ["a.py"]) == ["a.py"]


def test_this_module_imports_nothing_which_is_what_makes_it_a_leaf():
    """fallout GI-033 / fallout AC-061 (C-079) — the property, asserted rather than
    claimed.

    The leaf set is a CHECKED PROPERTY — imports only leaves, at any depth — and
    this module qualifies in the strongest way available: it imports nothing at
    all. That is what lets `transitions.py` and `width.py` (VERIFIER) and
    `directives.py` and `foundry_spawn.py` (lifecycle) all read one body, when
    fallout GI-033 makes the two layers mutually unreachable at module top and therefore
    makes `foundry_validate.py#_key_file_covers` unusable from the verifier
    half however convenient it would be.
    """
    tree = ast.parse(Path(_keyfiles.__file__).read_text(encoding="utf-8"))
    imported = [
        node for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        # `from __future__ import annotations` is a compiler directive, not a
        # dependency: it binds no module and cannot carry a layer with it.
        and not (isinstance(node, ast.ImportFrom) and node.module == "__future__")
    ]
    assert imported == [], [ast.dump(n) for n in imported]


def test_every_key_files_consumer_in_this_casting_reads_the_one_statement():
    """fallout FR-009 (D-170 / C-079) — all four sites, on the source.

    C-079's drive was a grep of every `key_files` consumer in the package, of
    which only `evidence.py#_sweep_touched_castings` read the directory spelling
    correctly. Four of the rest are this casting's, and the point of a shared
    leaf is defeated the moment one of them re-inlines the comparison — which is
    invisible behaviourally on a manifest with no directory entry, because there
    the two readings agree.

    Asserted on the SOURCE for that reason: a behavioural drive over a
    file-entry manifest passes whichever reading is in force.
    """
    for module, symbol in (
        (_directives, "_owning_casting"),
        (_transitions, "_start_cast_preconditions"),
        (_width, "_research_scope_touched"),
    ):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        fn = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == symbol
        )
        calls = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "covers_path" in calls, (module.__name__, symbol, sorted(calls))
        # ...and the membership test it replaced is gone from that body.
        body = ast.get_source_segment(source, fn) or ""
        assert "in casting_keyfiles" not in body, (module.__name__, symbol)

    # `foundry_spawn.py` is the fourth site and is not an orchestration module,
    # so it is read by path rather than through the package alias.
    from foundry_mcp.tools import foundry_spawn

    spawn = Path(foundry_spawn.__file__).read_text(encoding="utf-8")
    assert "from foundry_mcp.tools.orchestration.keyfiles import covers_path" in spawn
    assert "[f for f in changed if f in casting_keyfiles]" not in spawn


def test_the_validate_gate_and_the_validate_tool_answer_one_manifest_alike(run_env):
    """fallout FR-009 / GI-011 (C-079) — the divergence this closed, driven.

    `Foundry-Gate('validate')` maps to `_start_cast_preconditions`, which
    computes the no-file-overlap rule that `Foundry-Validate-Castings`
    dimension 3 computes. Both compared `key_files` by exact string membership;
    when casting 7 corrected its half at 5f6e7e9 the two doors began giving
    OPPOSITE answers about the same manifest — a directory-overlap manifest
    FAILED the tool and PASSED the gate. Two doors, one rule, two answers.

    Driven on the manifest that separates them: casting 1 names a directory and
    casting 2 names a file inside it, which is an overlap under coverage and
    invisible under equality.
    """
    project_root, fdir = run_env
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(json.dumps({"castings": [
        {"id": 1, "title": "one", "key_files": ["src/pkg/"],
         "tasks": [{"title": "t"}], "must_haves": {}},
        {"id": 2, "title": "two", "key_files": ["src/pkg/one.py"],
         "tasks": [{"title": "t"}], "must_haves": {}},
    ]}), encoding="utf-8")
    _write_state(fdir, phase="F0")

    outcome = _transitions._start_cast_preconditions(fdir, project_root)

    assert outcome["passed"] is False, outcome
    assert "File overlap between castings" in outcome["reason"], outcome
    # The refusal names the entry the overlap came THROUGH, because the covered
    # file appears in casting 1's key_files nowhere literally.
    assert "src/pkg/one.py" in outcome["reason"], outcome
    assert "src/pkg/" in outcome["reason"], outcome

    # The control: no directory entry, no overlap, and the rung passes exactly
    # as equality always made it pass.
    (fdir / "castings" / "manifest.json").write_text(json.dumps({"castings": [
        {"id": 1, "title": "one", "key_files": ["src/pkg/two.py"],
         "tasks": [{"title": "t"}], "must_haves": {}},
        {"id": 2, "title": "two", "key_files": ["src/pkg/one.py"],
         "tasks": [{"title": "t"}], "must_haves": {}},
    ]}), encoding="utf-8")
    clean = _transitions._start_cast_preconditions(fdir, project_root)
    assert "File overlap" not in (clean["reason"] or ""), clean


def test_a_research_casting_owning_a_directory_still_widens_the_audit(run_env):
    """fallout FR-009 / ST-007 (C-079) — the verifier that failed OPEN.

    `_research_scope_touched` matched `f.strip() in touched_set`, so a casting
    whose `key_files` are directory entries never registered as touched however
    much of it the GRIND rewrote — and RESEARCH_AUDIT was then recorded
    `skipped` with the detail "no file research_audit covers was touched": a
    NEGATIVE about a set the arm had read and misread. D-207 named that exact
    asymmetry on the sibling arm; a verifier declining to widen on evidence it
    could not see is the fail-open direction.
    """
    project_root, fdir = run_env
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(json.dumps({"castings": [
        {"id": 1, "research_context": "research/auth.md",
         "key_files": ["src/pkg/"]},
    ]}), encoding="utf-8")

    hit = _width._research_scope_touched(fdir, project_root, ["src/pkg/one.py"])
    assert hit["touched"] is True, hit
    assert hit["computable"] is True, hit
    # The detail names the file that matched AND the entry that covered it, so
    # a reader is not left to work out why a path nobody declared counted.
    assert "src/pkg/one.py" in hit["detail"], hit
    assert "src/pkg/" in hit["detail"], hit

    # The control: a computed MISS is still a miss, so the widening did not
    # become "every diff touches everything".
    miss = _width._research_scope_touched(fdir, project_root, ["other/one.py"])
    assert miss["touched"] is False, miss
    assert miss["computable"] is True, miss


def test_a_ui_casting_that_declares_a_directory_still_requires_sight(run_env):
    """fallout FR-020 / AC-025 (casting 10's concern C-081) — the one-argument
    finish, at the call site that already held the argument.

    The leaf decided SIGHT by asking whether any `key_files` entry ENDS in a UI
    extension. A directory entry never does, so a casting declaring its package
    once — the spelling the cast gate's eight-entry cap forces — reported "No
    frontend files in castings" however much UI sat inside it, and SIGHT was
    skipped on a run that needed it: a fail-OPEN on a required stream.

    Casting 10 closed its half by teaching `foundry_state.sight_required` to
    WALK a directory entry when given a `project_root`, and reported
    `undetermined_directories` without one. `gates.py#_streams_complete` already
    holds `project_root` — it passes it to `count_spec_requirements` on the very
    next line — so passing it one line earlier turns every undetermined answer
    into a measured one.

    Driven through the composed reader rather than the leaf, because the leaf's
    own half is casting 10's and already pinned; what is unasserted until here
    is that THIS package's shims pass the argument.
    """
    project_root, fdir = run_env
    ui = Path(project_root) / "src" / "web"
    ui.mkdir(parents=True, exist_ok=True)
    (ui / "App.tsx").write_text("export default () => null\n", encoding="utf-8")
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(json.dumps({
        "target_url": "http://localhost:3000",
        "castings": [{"id": 1, "title": "web", "key_files": ["src/web/"]}],
    }), encoding="utf-8")

    measured = _width._sight_required(fdir, project_root)
    assert measured["required"] is True, measured
    assert not measured.get("undetermined_directories"), measured

    # Without the run root the leaf cannot walk, and it says so rather than
    # reporting a measured negative — which is the state this call site left it
    # in, and the reason the argument is the fix.
    unmeasured = _width._sight_required(fdir)
    assert unmeasured.get("undetermined_directories"), unmeasured

    # The control casting 10 rejected the fail-closed alternative on: a BACKEND
    # run whose manifest names a directory with no UI in it must NOT be forced
    # into SIGHT. This run's own manifest is that shape.
    (fdir / "castings" / "manifest.json").write_text(json.dumps({
        "castings": [{"id": 2, "title": "orchestration",
                      "key_files": ["src/foundry_mcp/tools/orchestration/"]}],
    }), encoding="utf-8")
    backend = _width._sight_required(fdir, project_root)
    assert backend["required"] is False, backend


def test_every_sight_shim_in_this_package_passes_the_run_root(run_env):
    """fallout FR-020 / AC-025 (C-081) — all of them, on the source.

    Four call sites compose the leaf's sight reader, and a fix applied to some
    of them is invisible behaviourally on a manifest with no directory entry —
    exactly where the two readings agree. Asserted structurally for that reason.
    """
    import ast

    from foundry_mcp.tools.orchestration import gates as _gates
    from foundry_mcp.tools.orchestration import teams as _teams

    for module, symbol, expected in (
        (_gates, "_streams_complete", "_sight_required"),
        (_transitions, "_cast_preconditions", "_sight_required"),
        (_width, "_base_required_streams", "_sight_required"),
        (_teams, "_check_sight_required", "sight_required"),
    ):
        source = Path(module.__file__).read_text(encoding="utf-8")
        fn = next(
            n for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.FunctionDef) and n.name == symbol
        )
        calls = [
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == expected
        ]
        assert calls, (module.__name__, symbol, "no call to " + expected)
        for call in calls:
            passed = [a.id for a in call.args if isinstance(a, ast.Name)]
            passed += [
                k.arg for k in call.keywords
                if k.arg in ("project_root", "directory_suffix")
            ]
            assert "project_root" in passed, (
                module.__name__, symbol,
                "the run root is in hand here and not passed; without it a "
                "directory key_files entry reads as 'no frontend files' (C-081)",
            )
