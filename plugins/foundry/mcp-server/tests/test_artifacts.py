"""The run-artifact leaf, driven directly.

Requirements: ``forge-specs/foundry-run-fallout/spec.md``. The rows this module
proves are named, with the symbol each lands on, in casting 7's completion
report and in the ``# evidence-for:`` header of
``evidence/casting-7-artifact-leaf.log``. They are deliberately absent from the
prose here: ``tests/test_spec_id_convention.py`` demanded that every
three-digit requirement id in a docstring or comment in this directory carry
one of two qualifications, ``process-fixes`` or ``convergence``, and this run's
spec was installed with no legal qualification of its own — so the only
spellings that would have passed this directory's pin named a DIFFERENT spec's
requirement. Recorded in ``foundry-archive/foundry-run-fallout/concerns.md``,
and since closed: ``QUALIFIERS`` now carries ``fallout ``, so the guards added
at the foot of this module cite ``fallout GI-033`` under its own spec's name
rather than staying silent to stay green.

The answer this module is built from, verbatim:

    "Layered: verifier modules may import shared leaf modules (artifacts,
    foundry_state, vocab, schemas) and nothing in the presentation/lifecycle
    layer, EXCEPT that transitions dispatch the `halt` token and the terminal
    seal through a one-way seam into halt.py"

``foundry_mcp/tools/artifacts.py`` is the "artifacts" of that sentence: the
document primitives, standing beside ``foundry_state`` and ``vocab`` with
nothing above them in scope. Every test below drives the leaf DIRECTLY against
a ``tmp_path`` run directory and reads the persisted document — and the lock
sidecar — back off disk. The module carries its own ``run_env`` fixture rather
than sharing one through ``conftest.py``, which is this suite's per-concern
convention.

WHAT IS ACTUALLY AT RISK HERE, and it is not the logic. These bodies are the
orchestrator's bodies, moved unchanged; what a move can break is the SEAM — a
name that resolved through the old module's globals and now does not, an import
the new home lacks, a sidecar filename that drifted away from the scan that has
to skip it. So the tests below are anchored on the seam: what each primitive
does with a malformed document, what it writes, what it names its lock, and what
the module reaches for at import time.
"""

from __future__ import annotations

import ast
import json
import threading
from pathlib import Path

import pytest

from foundry_mcp.tools import artifacts
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.artifacts import (
    _artifact_guard,
    _document_problem,
    _document_transaction,
    _is_write_sidecar,
    _load_json,
    _resolve_spec_path,
    _run_artifact_problems,
    _save_json,
    _stream_marker,
    _TX_LOCK_SUFFIX,
)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def run_env(tmp_path):
    """Activate a foundry run under tmp_path; yield (project_root, fdir)."""
    project_root = tmp_path
    run_name = "artifact-leaf-run"
    fdir = project_root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


#: Every malformed container shape ``_load_json``'s own docstring names, plus
#: the absent file. Written as BYTES so the non-UTF-8 case is expressible at
#: all: a str fixture cannot carry the one shape that raises before json is
#: ever reached, which is the shape the tolerant read was written for.
MALFORMED_DOCUMENTS = {
    "truncated": b'{"cycle": 3,',
    "list": b"[]",
    "null": b"null",
    "number": b"42",
    "string": b'"a string"',
    "not-utf8": b"\xff\xfe\x00garbage",
    "empty": b"",
}


# --------------------------------------------------------------------------- #
# The total, tolerant read
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("shape", sorted(MALFORMED_DOCUMENTS))
def test_the_tolerant_read_answers_every_malformed_container_with_an_empty_document(
    run_env, shape
):
    """"Every malformed-container shape — truncated, ``[]``, ``null``, ``42``,
    ``"a string"``, non-UTF-8 — reads as an empty document, so no reader in
    this module can raise across the MCP boundary."

    The primitive's own docstring, and the whole reason a reader in the leaf is
    safe by construction rather than by a remembered try/except at each site.
    """
    _, fdir = run_env
    path = fdir / "state.json"
    path.write_bytes(MALFORMED_DOCUMENTS[shape])

    assert _load_json(path) == {}


def test_the_tolerant_read_answers_an_absent_document_with_an_empty_document(run_env):
    """A run legitimately has artifacts it has not written yet, so ABSENT and
    CORRUPT read alike here — the return value cannot distinguish them by
    design, which is why a caller that must tell the operator uses the guard.
    """
    _, fdir = run_env
    assert _load_json(fdir / "never-written.json") == {}


def test_a_readable_document_reads_back_as_itself(run_env):
    """The tolerance is a floor, not a filter: a well-formed object is returned
    unchanged. A read that answered {} for everything would pass every test
    above and be useless.
    """
    _, fdir = run_env
    path = fdir / "state.json"
    path.write_text(json.dumps({"cycle": 3, "phase": "F2"}), encoding="utf-8")

    assert _load_json(path) == {"cycle": 3, "phase": "F2"}


@pytest.mark.parametrize("shape", sorted(set(MALFORMED_DOCUMENTS) - {"empty"}))
def test_the_named_problem_names_the_file_that_cannot_be_read(run_env, shape):
    """"The named reason ``path`` is not a readable JSON object, or None."

    Tolerance alone would read a corrupt state.json as cycle 0 in silence; this
    is the rung that makes the file's NAME reachable by a caller that refuses.
    """
    _, fdir = run_env
    path = fdir / "state.json"
    path.write_bytes(MALFORMED_DOCUMENTS[shape])

    problem = _document_problem(path)
    assert problem is not None
    assert "state.json" in problem


def test_an_absent_document_is_not_a_named_problem(run_env):
    """An ABSENT file is not a problem. Conflating absent with corrupt is what
    would make a fresh run refuse to start.
    """
    _, fdir = run_env
    assert _document_problem(fdir / "never-written.json") is None


# --------------------------------------------------------------------------- #
# The atomic write
# --------------------------------------------------------------------------- #


def test_the_atomic_write_leaves_the_document_and_no_sidecar(run_env):
    """"Atomic JSON write — write to a UNIQUE .tmp, then rename."

    The rename is the commit. What must be true after it is that the document
    is readable and NOTHING else is left in the directory: a stray sidecar is a
    file the guard would then have to have an opinion about.
    """
    _, fdir = run_env
    path = fdir / "verdicts.json"

    _save_json(path, {"US-1": "VERIFIED"})

    assert json.loads(path.read_text(encoding="utf-8")) == {"US-1": "VERIFIED"}
    assert sorted(p.name for p in fdir.iterdir() if p.is_file()) == ["verdicts.json"]


def test_the_write_sidecar_carries_the_writers_own_pid_and_thread(run_env):
    """The tmp name carries pid and thread id, because the old shared
    ``path.with_suffix(".tmp")`` let a peer's rename move THIS call's
    half-written payload into place or delete it mid-write.

    Driven by holding the write open: the sidecar's name is read while it
    exists rather than inferred from the source.
    """
    _, fdir = run_env
    path = fdir / "stream-rollup.json"
    seen: list[str] = []

    real_rename = Path.rename

    def _watch(self, target):
        seen.append(self.name)
        return real_rename(self, target)

    original = Path.rename
    Path.rename = _watch
    try:
        _save_json(path, {"cycles": {}})
    finally:
        Path.rename = original

    assert len(seen) == 1, seen
    assert seen[0].startswith("stream-rollup.json.")
    assert seen[0].endswith(".tmp")
    assert str(threading.get_ident()) in seen[0]


# --------------------------------------------------------------------------- #
# The locked read-modify-write
# --------------------------------------------------------------------------- #


def test_a_transaction_that_mutates_writes_on_clean_exit(run_env):
    """"Mutate it in place; it is written back through ``_save_json`` on clean
    exit."
    """
    _, fdir = run_env
    path = fdir / "state.json"
    path.write_text(json.dumps({"cycle": 0}), encoding="utf-8")

    with _document_transaction(path) as doc:
        doc["cycle"] = 1

    assert json.loads(path.read_text(encoding="utf-8")) == {"cycle": 1}


def test_an_exception_inside_a_transaction_writes_nothing(run_env):
    """"An exception inside the block propagates and NOTHING is written, so a
    failed mutation cannot leave a half-updated artifact."
    """
    _, fdir = run_env
    path = fdir / "state.json"
    path.write_text(json.dumps({"cycle": 0}), encoding="utf-8")

    with pytest.raises(RuntimeError):
        with _document_transaction(path) as doc:
            doc["cycle"] = 99
            raise RuntimeError("mid-transaction")

    assert json.loads(path.read_text(encoding="utf-8")) == {"cycle": 0}


def test_a_transaction_that_mutates_nothing_leaves_the_file_untouched(run_env):
    """"A block that mutates NOTHING writes nothing: the document is
    snapshotted on entry and compared on exit."

    Driven on the case the docstring names as the reason: a CORRUPT artifact a
    caller only read is left intact rather than silently replaced by ``{}``.
    """
    _, fdir = run_env
    path = fdir / "state.json"
    path.write_bytes(b'{"cycle": 3,')

    with _document_transaction(path) as doc:
        assert doc == {}

    assert path.read_bytes() == b'{"cycle": 3,'


def test_a_nested_transaction_on_one_path_yields_the_same_document(run_env):
    """"Re-entrant per path: a nested transaction on a path this thread already
    holds yields the same in-flight dict and defers the write to the outermost
    exit."

    Without it the package's own nested state.json write blocks forever on its
    own flock. Both halves are asserted: the SAME object inside, and one
    document carrying both mutations after the outermost exit.
    """
    _, fdir = run_env
    path = fdir / "state.json"

    with _document_transaction(path) as outer:
        outer["outer"] = True
        with _document_transaction(path) as inner:
            assert inner is outer
            inner["inner"] = True
        # The inner exit deferred: nothing on disk yet.
        assert not path.exists()

    assert json.loads(path.read_text(encoding="utf-8")) == {"outer": True, "inner": True}


def test_the_lock_sidecar_is_the_document_name_plus_the_declared_suffix(run_env):
    """The lock FILENAME is load-bearing across process boundaries, not an
    implementation detail.

    Two server processes on one repository exclude each other ONLY through this
    file: ``path.with_name(path.name + _TX_LOCK_SUFFIX)``. A renamed or
    "improved" sidecar leaves two writers believing they were serialized when
    they were not, and nothing in either process would notice. Read off disk
    while the transaction is open, and the suffix is read from the declaration
    rather than typed here.
    """
    _, fdir = run_env
    path = fdir / "state.json"

    with _document_transaction(path):
        present = sorted(p.name for p in fdir.iterdir() if p.is_file())

    assert f"state.json{_TX_LOCK_SUFFIX}" in present
    assert _TX_LOCK_SUFFIX == ".lock"


def test_the_scan_skips_exactly_the_sidecars_the_writers_really_create(run_env):
    """The scan's idea of what is scaffolding, checked against what the writers
    actually put on disk.

    THIS REPLACED A TEST WHOSE SUBJECT WAS DELETED. While the orchestrator
    still carried its own copy of these primitives, the test here compared the
    two modules' ``_TX_LOCK_SUFFIX`` and ``_TX_TMP_SUFFIX`` as objects: the
    duplicate window's whole safety property was that both writers computed the
    same lock path. The carve landed, there is no second copy to compare
    against, and that half is now
    ``tests/orchestration/test_module_boundaries.py::test_the_helpers_group_zero_consolidated_have_exactly_one_definition``'s
    to hold.

    WHAT SURVIVES THE WINDOW is the reason those two suffixes were declared as
    shared constants in the first place, and it is stated in the source above
    them: they are consumed by BOTH the writers that create the sidecars and by
    ``_run_artifact_problems``'s exclusion, "so the scan's idea of what is
    scaffolding cannot drift from what the writers actually create". That is a
    permanent property of this module and nothing else in this file asserts it:
    the sidecars in
    ``test_a_write_primitive_s_own_scaffolding_is_not_a_run_artifact`` are
    spelled BY HAND, so a writer that changed its suffix would leave that test
    green on the old spelling while the guard began reporting live sidecars and
    refusing every door.

    So both names are taken from the writers themselves — the lock from the
    directory while a transaction holds it, the tmp from the rename that
    commits it — and it is those names, not typed ones, that the scan must skip.
    """
    _, fdir = run_env
    path = fdir / "state.json"

    before = {p.name for p in fdir.iterdir()}
    with _document_transaction(path) as doc:
        doc["cycle"] = 1
        lock_names = sorted({p.name for p in fdir.iterdir()} - before)

    committed: list[str] = []
    original = Path.rename

    def _watch(self, target):
        committed.append(self.name)
        return original(self, target)

    Path.rename = _watch
    try:
        _save_json(path, {"cycle": 2})
    finally:
        Path.rename = original

    created = lock_names + committed
    assert len(created) == 2, created

    # Each one, under the name its WRITER chose, and holding bytes that would
    # be named on any artifact — the guard must still be silent, because these
    # are not artifacts.
    for name in created:
        sidecar = fdir / name
        sidecar.write_bytes(b"\xff\xfe half a document")
        assert _is_write_sidecar(sidecar), name
    assert _run_artifact_problems(fdir) == []

    # The control, or the assertion above proves only that the scan is asleep:
    # the same bytes under a name no writer creates ARE named.
    (fdir / "verdicts.json").write_bytes(b"\xff\xfe half a document")
    assert any("verdicts.json" in p for p in _run_artifact_problems(fdir))


# --------------------------------------------------------------------------- #
# Corruption classification and the house guard
# --------------------------------------------------------------------------- #


def test_the_guard_is_silent_on_a_healthy_run(run_env):
    """A guard that refuses a healthy run locks every door at once, so the
    negative case is the one that has to hold first.
    """
    _, fdir = run_env
    (fdir / "state.json").write_text(json.dumps({"cycle": 0}), encoding="utf-8")
    (fdir / "directives.md").write_text("nothing urgent\n", encoding="utf-8")

    assert _artifact_guard(fdir) is None


def test_the_guard_refuses_in_the_house_shape_and_names_the_file(run_env):
    """"``error`` names the offending FILES and what is wrong with each,
    ``hint`` names the action."

    The refusal shape is the contract: a tool never raises across the MCP
    boundary, it returns a dict the operator can act on.
    """
    _, fdir = run_env
    (fdir / "state.json").write_bytes(b'{"cycle": 3,')

    refusal = _artifact_guard(fdir)

    assert refusal is not None
    assert set(refusal) == {"error", "hint", "corrupt_artifacts"}
    assert "state.json" in refusal["error"]
    assert refusal["corrupt_artifacts"] and all(
        isinstance(p, str) for p in refusal["corrupt_artifacts"]
    )
    assert "repair" in refusal["hint"].lower()


def test_a_non_json_artifact_is_classified_on_the_text_floor(run_env):
    """A suffix outside the strict table falls THROUGH to the text floor rather
    than out of the scan: directives.md is not JSON and still must decode.
    """
    _, fdir = run_env
    (fdir / "directives.md").write_bytes(b"\xff\xfe URGENT: stop the cast wave")

    problems = _run_artifact_problems(fdir)

    assert any("directives.md" in p for p in problems), problems


def test_a_write_primitive_s_own_scaffolding_is_not_a_run_artifact(run_env):
    """The scaffolding a write puts BESIDE an artifact is not an artifact.

    Nothing reads a ``.tmp`` sidecar or a ``.lock`` file back, both are
    mid-flight by construction while a peer writes, and the guard's hint —
    "repair or delete the named file" — is advice that races the writer.
    """
    _, fdir = run_env
    (fdir / f"state.json{_TX_LOCK_SUFFIX}").write_bytes(b"\xff\xfe")
    (fdir / "state.json.9999.1234.tmp").write_bytes(b'{"half":')

    assert _run_artifact_problems(fdir) == []


def test_a_binary_artifact_a_run_legitimately_holds_is_not_reported(run_env):
    """A screenshot is a run artifact and does not decode as text. Reporting it
    would refuse every door on a healthy run.
    """
    _, fdir = run_env
    (fdir / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

    assert _run_artifact_problems(fdir) == []


def test_a_directory_occupying_a_document_position_is_named(run_env):
    """A DIRECTORY is a container and is walked past — except one occupying a
    path a reader OPENS as a document, where the read raises instead of
    refusing by name.

    Both axes of the position question are driven: a suffixed document name and
    a suffix-less sentinel.
    """
    _, fdir = run_env
    (fdir / "state.json").mkdir()
    (fdir / ".last-next-at").mkdir()

    problems = _run_artifact_problems(fdir)

    assert any("state.json" in p for p in problems), problems
    assert any(".last-next-at" in p for p in problems), problems


def test_a_scratch_directory_whose_name_holds_a_dot_stays_silent(run_env):
    """A DOT IS NOT A DOCUMENT TYPE. ``Path("14.0.0").suffix`` is ``".0"``, and
    classifying a directory by its punctuation named a healthy run corrupt and
    refused every door at once.
    """
    _, fdir = run_env
    (fdir / "test_observations" / "generated" / ".hypothesis" / "unicode_data" / "14.0.0").mkdir(
        parents=True
    )

    assert _run_artifact_problems(fdir) == []


def test_the_guard_walks_past_the_runs_own_worktrees_checkout(run_env):
    """A checkout this package nests UNDER the run dir is not one of the run's
    artifacts. Every worktree it creates is rooted at ``<run>/worktrees``, and
    walking into one made the boundary sweep's own virtualenv refuse every
    door for the duration of the sweep.

    The control matters as much as the case: a directory spelled the same way
    somewhere DEEPER is not a position the writer roots at and stays in scope.
    """
    _, fdir = run_env
    nested = fdir / "worktrees" / "sweep-evidence" / "plugins"
    nested.mkdir(parents=True)
    (nested / "fixture.md").write_bytes(b"\xff\xfe not utf-8")

    assert _run_artifact_problems(fdir) == []

    deeper = fdir / "traces" / "worktrees"
    deeper.mkdir(parents=True)
    (deeper / "notes.md").write_bytes(b"\xff\xfe not utf-8")

    assert any("notes.md" in p for p in _run_artifact_problems(fdir))


# --------------------------------------------------------------------------- #
# The one spelling of a stream's completion sentinel
# --------------------------------------------------------------------------- #


def test_the_stream_marker_has_one_spelling_and_the_guard_knows_the_family():
    """"Five call sites spelled ``f".{stream}-complete"`` and four more spelled
    two of its members as bare literals, so the guard's derived family and the
    readers agreed only by inspection."

    Driven over the whole vocabulary rather than over two members, because
    listing members is the hand-kept-list defect the derivation replaced.
    """
    from foundry_mcp.schemas.vocab import STREAM_WIRE_IDS

    assert STREAM_WIRE_IDS, "an empty vocabulary would make this pass vacuously"
    for wire_id in STREAM_WIRE_IDS:
        assert _stream_marker(wire_id) == f".{wire_id}-complete"
        assert artifacts._is_document_position(Path("run") / _stream_marker(wire_id))


# --------------------------------------------------------------------------- #
# Spec-path resolution
# --------------------------------------------------------------------------- #


def test_the_spec_path_prefers_the_run_dir_copy(run_env):
    """"Prefers ``<run_dir>/spec.md``." The first rung, and the one that holds
    on a run whose spec was copied in.
    """
    project_root, fdir = run_env
    (fdir / "spec.md").write_text("# Spec\n", encoding="utf-8")

    assert _resolve_spec_path(project_root) == fdir / "spec.md"


def test_the_spec_path_falls_back_to_the_declared_external_path(run_env):
    """"falls back to ``state.json['spec_path']`` resolved against
    ``project_root``."

    This is the live shape of a real run: the spec sits outside the run
    directory and is reachable only through the declaration.
    """
    project_root, fdir = run_env
    external = Path(project_root) / "forge-specs" / "probe" / "spec.md"
    external.parent.mkdir(parents=True)
    external.write_text("# Spec\n", encoding="utf-8")
    (fdir / "state.json").write_text(
        json.dumps({"spec_path": "forge-specs/probe/spec.md"}), encoding="utf-8"
    )

    assert _resolve_spec_path(project_root) == external


def test_the_spec_path_is_none_when_neither_rung_resolves(run_env):
    """A declaration pointing at a file that is not there resolves to nothing,
    and the caller decides what that means. Silently returning the run-dir path
    would hand every caller a path that does not exist.
    """
    project_root, fdir = run_env
    (fdir / "state.json").write_text(
        json.dumps({"spec_path": "forge-specs/gone/spec.md"}), encoding="utf-8"
    )

    assert _resolve_spec_path(project_root) is None


def test_the_spec_path_is_none_with_no_active_run(tmp_path):
    """No run, no spec. The resolver asks ``get_run_dir`` first, which is the
    only package edge this module is allowed and the one this rung needs.
    """
    foundry_state.clear_active_run()
    assert _resolve_spec_path(str(tmp_path)) is None


# --------------------------------------------------------------------------- #
# The layering assertion — what makes this module a leaf
# --------------------------------------------------------------------------- #

#: What a leaf may reach for at LOAD time. ``schemas.vocab`` and
#: ``tools.foundry_state`` are the two shared leaves named by the layering
#: answer this module is built from; everything else is the standard library.
LEAF_PACKAGE_IMPORTS = frozenset(
    {"foundry_mcp.schemas.vocab", "foundry_mcp.tools.foundry_state"}
)


def test_the_leaf_reaches_for_nothing_above_it_at_module_top():
    """The property that makes this module the leaf, asserted rather than
    described.

    Read off the SOURCE with ``ast`` rather than off the imported module,
    because a module object cannot tell a load-time edge from a call-time one —
    and the difference is the entire point. A module-top import of anything in
    the presentation or lifecycle layer would put the state machine back
    underneath the persistence layer, which is the shape this split exists to
    end.
    """
    tree = ast.parse(Path(artifacts.__file__).read_text(encoding="utf-8"))

    reached: set[str] = set()
    for node in tree.body:  # module TOP only — a nested import is call-time
        if isinstance(node, ast.Import):
            reached.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            reached.add(node.module)

    package = sorted(name for name in reached if name.startswith("foundry_mcp"))
    assert package, "an empty result would make this pass vacuously"
    assert set(package) <= LEAF_PACKAGE_IMPORTS, package


def test_the_only_edge_above_the_leaf_is_the_declared_lazy_one():
    """The one call-time import, and it is named rather than merely tolerated.

    ``_manifest_shape_problem_lazy`` reaches ``foundry_spawn`` INSIDE the
    function because ``foundry_spawn`` imports the orchestrator at module top,
    and a load-time edge here would close the graph. This asserts there is
    exactly one such edge and that it sits where it is claimed to sit — a
    second lazy import added later is a layering decision, not a detail.
    """
    tree = ast.parse(Path(artifacts.__file__).read_text(encoding="utf-8"))

    lazy: list[str] = []
    for parent in ast.walk(tree):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(parent):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("foundry_mcp"):
                    lazy.append(f"{parent.name} -> {node.module}")
            elif isinstance(node, ast.Import):
                lazy.extend(
                    f"{parent.name} -> {a.name}"
                    for a in node.names
                    if a.name.startswith("foundry_mcp")
                )

    assert lazy == [
        "_manifest_shape_problem_lazy -> foundry_mcp.tools.foundry_spawn"
    ], lazy


# --------------------------------------------------------------------------- #
# fallout GI-033 — THE LEAF IS THE ONE HOME, AND THE INVENTORY IS HOW THAT GETS
# TRUE RATHER THAN MERELY ASSERTED.
#
# Both second copies this inventory was written for are GONE, closed by the
# casting that owns ``tools/foundry.py`` under concern C-003: the tolerant read
# is imported from here (fallout D-011) and the lock domain is bound from here
# (fallout D-010), so ``_SECOND_LOCK_DOMAINS`` is empty and
# ``_SECOND_READ_LAYER``'s one row has shrunk to what it still accounts for.
# The guards stay, in the shape this package already uses for exactly this
# situation
# (``tests/orchestration/test_module_boundaries.py#_KNOWN_DUPLICATION``): an
# INVENTORY, not an exemption list. It behaves one way in each direction, and
# the two directions are the whole point:
#
#   - a NEW second home fails immediately, so the class cannot grow while
#     nobody is looking;
#   - a row that has stopped accounting for anything ALSO fails, so the day the
#     deletion lands the inventory has to shrink with it. An allowlist that
#     outlives the thing it excuses is how an exception becomes the rule.
#
# The name-collision sweep in ``test_module_boundaries`` already covers the READ
# layer, because those four names collide. It was BLIND to the lock domain: one
# module spelled it ``_ARTIFACT_LOCK`` and the other ``_LEDGER_LOCK``, so a
# sweep keyed on names saw two different symbols where there is one rule. That
# blind spot is why the second guard below is keyed on the SHAPE — a module-top
# ``threading.RLock()`` / ``threading.local()`` pair — rather than on a name,
# and it is why the guard outlives the row it was written to hold: the next
# module to declare a domain of its own will not be named after this one
# either.
#
# AND THE COLLISION SWEEP CANNOT TELL TWO CONTRACTS APART, which is the other
# half of what the rows below now record. ``_artifact_guard`` is defined here
# and in ``tools/foundry.py`` and the two are DIFFERENT FUNCTIONS: this one
# takes a run dir and scans the whole run; that one takes ``*names``, is scoped
# to the artifacts the calling tool touches, and runs a ledger-container rung
# that may not move into a leaf (fallout GI-033 — a leaf that knows what a
# ledger is has stopped being one). A sweep keyed on names reports that as one
# rule duplicated. It is one NAME shared by two, which is a different finding
# and is recorded as one rather than closed by deleting a guard the package
# needs.
# --------------------------------------------------------------------------- #


#: A shipped module keeping its own copy of the tolerant-read layer beside this
#: one, with the reason it is still there. fallout D-011.
_SECOND_READ_LAYER: dict[str, str] = {
    "foundry.py": (
        "`_artifact_guard` ONLY, and it is not a second copy of this module's — "
        "it takes `*names`, is scoped to the artifacts the calling tool touches "
        "so a corrupt roll-up cannot block a filing that never opens it, and it "
        "runs the ledger-container rung (`_LEDGER_KEYS` / `ledger_shape_problem`, "
        "D-096) that GI-033 keeps out of a leaf. The three that WERE byte-"
        "identical — `_read_document`, `_document_problem`, `_load_json` — are "
        "imported from here as of concern C-003; this row is the shared NAME, "
        "which the sweep cannot distinguish from a shared rule. Closing it means "
        "renaming one of the two, which is a decision for the castings that own "
        "both files rather than for either alone."
    ),
}


#: The four names that ARE the tolerant-read layer. A module defining any of
#: them is keeping a second copy of it, whatever it calls the file.
_READ_LAYER_SYMBOLS = ("_read_document", "_document_problem", "_load_json",
                       "_artifact_guard")


#: A shipped module declaring its own run-artifact lock domain beside this
#: module's, with the reason it is still there. fallout D-010.
#: EMPTY, and that is the state this table was written to reach. `foundry.py`
#: declared `_LEDGER_LOCK` / `_LEDGER_TX` over the same documents — verdicts.json
#: has one writer in each — and the two excluded on disk only because two
#: independently typed spellings of the lock filename agreed, one of them a bare
#: ".lock" literal at `_locked_document` and `write_document`. It now binds this
#: module's `_ARTIFACT_LOCK` / `_ARTIFACT_TX` and derives the sidecar name from
#: `_TX_LOCK_SUFFIX` (concern C-003), so there is one domain, one in-flight map
#: keyed on `str(path)`, and a cross-domain nesting composes into one write
#: instead of blocking on a flock the thread already holds.
_SECOND_LOCK_DOMAINS: dict[str, str] = {}


def _shipped_modules() -> list[Path]:
    """Every shipped ``.py`` under the installed package, tests excluded."""
    package = Path(artifacts.__file__).resolve().parent.parent
    return sorted(
        path for path in package.rglob("*.py") if "__pycache__" not in path.parts
    )


def _top_level_bindings(path: Path) -> set[str]:
    """Names ``path`` DEFINES at module top — ``def``, ``class``, assignment.

    An IMPORT is not a definition. A module that imports a name is reaching the
    one definition, which is the outcome these guards exist to produce rather
    than to forbid.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return names


def _declares_lock_domain(path: Path) -> bool:
    """True when ``path`` opens a lock domain of its own at module top.

    A domain is the PAIR — a re-entrant lock ordering threads and a
    thread-local map making the critical section re-entrant per path. Detected
    by shape rather than by name, which is the whole reason this guard exists
    beside the name-collision sweep: `_ARTIFACT_LOCK` and `_LEDGER_LOCK` are
    two names for one rule and no sweep keyed on names will ever pair them.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if (
            isinstance(func, ast.Attribute)
            and isinstance(func.value, ast.Name)
            and func.value.id == "threading"
            and func.attr in {"RLock", "local"}
        ):
            found.add(func.attr)
    return found == {"RLock", "local"}


def test_the_leaf_is_the_one_home_of_the_tolerant_read():
    """fallout GI-033 — one tolerant read for the package, and the inventory.

    `_load_json` has been defined twice in this package before, the two bodies
    were byte-identical until they were not, and the divergence was found by a
    defect rather than by a guard. This is that guard, written from the leaf's
    side: the leaf defines the layer, everyone else imports it, and any module
    that keeps its own copy is named here with the casting that deletes it.
    """
    modules = _shipped_modules()
    assert len(modules) >= 15, [str(m) for m in modules]

    leaf = Path(artifacts.__file__).resolve()
    leaf_bindings = _top_level_bindings(leaf)
    assert set(_READ_LAYER_SYMBOLS) <= leaf_bindings, sorted(leaf_bindings)

    elsewhere = {
        path.name: sorted(set(_READ_LAYER_SYMBOLS) & _top_level_bindings(path))
        for path in modules
        if path != leaf and set(_READ_LAYER_SYMBOLS) & _top_level_bindings(path)
    }

    unaccounted = {
        name: symbols
        for name, symbols in elsewhere.items()
        if name not in _SECOND_READ_LAYER
    }
    assert unaccounted == {}, (
        "module(s) keeping a second copy of the tolerant-read layer beside "
        "tools/artifacts.py, which is its one home. Delete the copy and import "
        "from the leaf, or — if the copy genuinely cannot go yet — record it in "
        f"_SECOND_READ_LAYER with the casting that deletes it: {unaccounted}"
    )

    stale = sorted(name for name in _SECOND_READ_LAYER if name not in elsewhere)
    assert stale == [], (
        f"_SECOND_READ_LAYER entr(y/ies) accounting for nothing: {stale}. The "
        "second copy is gone; take the row with it, or this table outlives what "
        "it was written to record."
    )


def test_the_leaf_is_the_one_run_artifact_lock_domain():
    """fallout GI-033 — one lock domain for the package, and the inventory.

    A second domain over the same documents is not a second implementation of a
    convenience; it is a second answer to "who may write this file now". Today
    the two agree, and they agree because two independently-typed spellings of
    one lock filename happen to match — which is a coincidence with a
    maintenance schedule, not an invariant. Keyed on the SHAPE of a domain
    rather than on the names of one, because the names are exactly what differ.
    """
    modules = _shipped_modules()
    leaf = Path(artifacts.__file__).resolve()
    assert _declares_lock_domain(leaf), "the leaf must hold the one domain"

    elsewhere = sorted(
        path.name
        for path in modules
        if path != leaf and _declares_lock_domain(path)
    )

    unaccounted = [name for name in elsewhere if name not in _SECOND_LOCK_DOMAINS]
    assert unaccounted == [], (
        "module(s) declaring a run-artifact lock domain of their own beside "
        "tools/artifacts.py, which holds the one. Bind to _ARTIFACT_LOCK / "
        "_ARTIFACT_TX and spell the sidecar through _TX_LOCK_SUFFIX, or — if "
        "the second domain genuinely cannot go yet — record it in "
        f"_SECOND_LOCK_DOMAINS with the casting that closes it: {unaccounted}"
    )

    stale = sorted(name for name in _SECOND_LOCK_DOMAINS if name not in elsewhere)
    assert stale == [], (
        f"_SECOND_LOCK_DOMAINS entr(y/ies) accounting for nothing: {stale}. The "
        "second domain is gone; take the row with it."
    )


def test_the_lock_sidecar_suffix_has_one_spelling_for_every_domain():
    """fallout GI-033 — the constant every locked writer spells the sidecar by.

    The flock on `<name>.lock` is the only thing ordering two lock domains
    against each other, so the FILENAME is the contract between them and a bare
    literal in one of them is the drift waiting to happen. Asserted from the
    leaf's side: this module builds the name from `_TX_LOCK_SUFFIX` and never
    from a literal, and the modules that still type one are the inventoried
    second domains and nobody else.
    """
    leaf = Path(artifacts.__file__).resolve()
    tree = ast.parse(leaf.read_text(encoding="utf-8"))

    literals = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and node.value == _TX_LOCK_SUFFIX
    ]
    # Exactly one: the declaration itself. Any second occurrence is a writer
    # that typed the suffix instead of reaching the constant beside it.
    assert len(literals) == 1, literals

    for path in _shipped_modules():
        if path == leaf or not _declares_lock_domain(path):
            continue
        assert path.name in _SECOND_LOCK_DOMAINS, path.name
