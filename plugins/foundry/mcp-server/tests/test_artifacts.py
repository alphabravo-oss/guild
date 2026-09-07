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
import re
import threading
from pathlib import Path

import pytest

from foundry_mcp.schemas import vocab
from foundry_mcp.schemas.vocab import (
    REPORT_REQUIRED_SECTIONS,
    REPORT_SECTION_TITLES,
)
from foundry_mcp.tools import artifacts
from foundry_mcp.tools import foundry_state
from foundry_mcp.tools.artifacts import (
    _artifact_guard,
    _artifact_lock,
    _document_problem,
    _document_transaction,
    _is_write_sidecar,
    _load_json,
    _hash_file,
    _hash_str,
    _resolve_spec_path,
    _run_artifact_problems,
    _save_json,
    _spec_requirement_ids,
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
# The requirement ids the run's spec declares — fallout GI-033, concern C-018
#
# THREE surfaces need this climb: F0.9's ownership dimensions, the F6 span
# section, and the DONE gate's requirement count and P3 verdict synthesis. They
# sit in layers with no legal edge between them — a verifier module may not
# import a lifecycle module and the reverse is worse — so the ladder cannot live
# in any one of them without a second copy appearing in another. It lives here,
# below all three, which is what a leaf is for.
# --------------------------------------------------------------------------- #


def test_the_ids_are_read_from_the_run_dir_copy_first(run_env):
    """The first rung, and the ids it yields."""
    project_root, fdir = run_env
    (fdir / "spec.md").write_text(
        "- **FR-001** a thing\n- **AC-002** another\n", encoding="utf-8"
    )

    text, ids, path, problem = _spec_requirement_ids(project_root)

    assert problem is None
    assert path == fdir / "spec.md"
    assert ids == {"FR-001", "AC-002"}
    assert "FR-001" in text


def test_the_ids_follow_the_declaration_out_of_the_run_directory(run_env):
    """The second rung — the live file, outside the run dir, which is the shape
    a real run has and the one D-145 was filed on.
    """
    project_root, fdir = run_env
    external = Path(project_root) / "forge-specs" / "probe" / "spec.md"
    external.parent.mkdir(parents=True)
    external.write_text("- **OT-003** observable\n", encoding="utf-8")
    (fdir / "state.json").write_text(
        json.dumps({"spec_path": "forge-specs/probe/spec.md"}), encoding="utf-8"
    )

    _text, ids, path, problem = _spec_requirement_ids(project_root)

    assert problem is None
    assert path == external
    assert ids == {"OT-003"}


def test_the_climb_and_the_resolver_never_pick_different_files(run_env):
    """The anti-fork property, asserted on both rungs.

    Two surfaces answering "which file is this run's spec" differently is the
    whole defect this move closes: one could count requirements the other never
    saw. `_resolve_spec_path` and `_spec_requirement_ids` share `_spec_path_from`
    rather than each climbing, and this is what says so.
    """
    project_root, fdir = run_env
    (fdir / "spec.md").write_text("- **FR-001** a thing\n", encoding="utf-8")
    assert _spec_requirement_ids(project_root)[2] == _resolve_spec_path(project_root)

    (fdir / "spec.md").unlink()
    external = Path(project_root) / "forge-specs" / "probe" / "spec.md"
    external.parent.mkdir(parents=True)
    external.write_text("- **FR-001** a thing\n", encoding="utf-8")
    (fdir / "state.json").write_text(
        json.dumps({"spec_path": "forge-specs/probe/spec.md"}), encoding="utf-8"
    )
    assert _spec_requirement_ids(project_root)[2] == _resolve_spec_path(project_root)


def test_an_absent_spec_declares_no_requirements_rather_than_raising(run_env):
    """A run legitimately has no spec before F0, and "nothing declared" is what
    is true of it. The PATH TRIED comes back rather than None, so a caller
    shaping a refusal has a file to name — which is where this differs from
    `_resolve_spec_path`, whose narrower question keeps its None.
    """
    project_root, fdir = run_env

    text, ids, path, problem = _spec_requirement_ids(project_root)

    assert (text, ids, problem) == ("", set(), None)
    assert path == fdir / "spec.md"
    assert _resolve_spec_path(project_root) is None


def test_a_spec_that_cannot_be_decoded_is_named_rather_than_raised(run_env):
    """D-145 exactly: one non-UTF-8 byte in the spec used to raise
    UnicodeDecodeError out of the DONE gate's requirement count.
    """
    project_root, fdir = run_env
    (fdir / "spec.md").write_bytes(b"\xff\xfe- **FR-001**")

    text, ids, path, problem = _spec_requirement_ids(project_root)

    assert problem is not None
    assert "spec.md" in problem
    assert (text, ids) == ("", set())
    assert path == fdir / "spec.md"


def test_documents_already_read_are_used_rather_than_read_again(run_env):
    """`fdir` and `state` are passed by a caller that has already opened them —
    the F0.9 gate reads state.json in its prelude — and the ladder must honour
    what it is handed rather than going back to disk for a second answer.
    """
    project_root, fdir = run_env
    external = Path(project_root) / "forge-specs" / "handed" / "spec.md"
    external.parent.mkdir(parents=True)
    external.write_text("- **CT-004** handed in\n", encoding="utf-8")
    # Nothing on disk says this; only the state the caller hands over does.
    assert not (fdir / "state.json").exists()

    _text, ids, path, problem = _spec_requirement_ids(
        project_root, fdir, {"spec_path": "forge-specs/handed/spec.md"}
    )

    assert problem is None
    assert path == external
    assert ids == {"CT-004"}


def test_no_active_run_declares_no_requirements(tmp_path):
    """No run, no spec, no ids — and no path to name either."""
    foundry_state.clear_active_run()

    assert _spec_requirement_ids(str(tmp_path)) == ("", set(), None, None)


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


def test_no_edge_above_the_leaf_survives_at_any_depth():
    """There is no call-time import out of this module either, and there was.

    ``_manifest_shape_problem_lazy`` reached ``foundry_spawn`` INSIDE its body,
    because ``foundry_spawn`` imports the orchestrator at module top and a
    load-time edge here would have closed the graph. Concern C-060 moved the
    validator into this module, so the edge has no reason to exist — and the
    assertion that used to NAME it now asserts its absence, which is the
    stronger form: a leaf that reaches nothing above it at any depth.

    THE DEPTH IS THE POINT. `fallout GI-033`'s package-wide guard measures leaf
    membership at both depths precisely because a module-top reading called
    `escalation.py` a leaf for a whole split while one lazy import sat inside a
    function. This module is measured here, by its own suite, on the same rule.
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

    assert lazy == [], lazy


# --------------------------------------------------------------------------- #
# fallout GI-033 — THE LEAF IS THE ONE HOME, AND THE INVENTORY IS HOW THAT GETS
# TRUE RATHER THAN MERELY ASSERTED.
#
# Both second copies this inventory was written for are GONE, closed by the
# casting that owns ``tools/foundry.py`` under concern C-003: the tolerant read
# is imported from here (fallout D-011) and the lock domain is bound from here
# (fallout D-010). The one row that outlived them recorded a shared NAME rather
# than a shared rule, and it went with the rename that closed fallout D-061 — so
# ``_SECOND_LOCK_DOMAINS`` and ``_SECOND_READ_LAYER`` are BOTH empty now.
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
# layer, because those four names collide. It was BLIND to the lock domain: the
# leaf spelled it ``_ARTIFACT_LOCK`` and the other module spelled its own pair
# under names of its own, so a sweep keyed on names saw two different symbols
# where there is one rule. That
# blind spot is why the second guard below is keyed on the SHAPE — a module-top
# ``threading.RLock()`` / ``threading.local()`` pair — rather than on a name,
# and it is why the guard outlives the row it was written to hold: the next
# module to declare a domain of its own will not be named after this one
# either.
#
# AND THE COLLISION SWEEP COULD NOT TELL TWO CONTRACTS APART, which is what the
# last row recorded until fallout D-061 closed it. ``_artifact_guard`` was
# defined both in the leaf and in ``tools/foundry.py``, and the two were
# DIFFERENT FUNCTIONS: the leaf's takes a run dir and scans the whole run; the
# other took ``*names``, was scoped to the artifacts the calling tool touches,
# and ran a ledger-container rung that may not move into a leaf (fallout
# GI-033 — a leaf that knows what a ledger is has stopped being one). A sweep
# keyed on names read that as one rule duplicated when it was one NAME over two
# contracts, so it was recorded rather than closed by deleting a guard the
# package needs. Neither deletion was ever on offer — the arities differ — and
# the exit that did close it was a name stating the narrower job,
# ``_named_artifact_guard``. Recording a finding is not closing it, and the row
# below is empty because someone went and closed this one.
# --------------------------------------------------------------------------- #


#: A shipped module keeping its own copy of the tolerant-read layer beside this
#: one, with the reason it is still there. fallout D-011.
#: EMPTY, and like the domain table below that is the state it was written to
#: reach. Its last row was `foundry.py`, and that row was never a second copy of
#: the rule: the three byte-identical bodies — `_read_document`,
#: `_document_problem` and `_load_json` — were imported from the leaf under
#: concern C-003, which left one shared NAME, `_artifact_guard`, over two
#: different contracts. A sweep keyed on names cannot see that difference, and
#: neither function could be deleted into the other — the arities differ, and
#: the ledger-container rung the other one runs (`_LEDGER_KEYS` /
#: `ledger_shape_problem`, D-096) is what fallout GI-033 keeps out of a leaf. So
#: the row said "not that one" for three cycles, until fallout D-061 renamed the
#: narrower function to `_named_artifact_guard` — the one exit that leaves the
#: package a single definition of `_artifact_guard` and no row for this table to
#: carry.
_SECOND_READ_LAYER: dict[str, str] = {}


#: The four names that ARE the tolerant-read layer. A module defining any of
#: them is answering the same question this module answers under the same name
#: — which is a finding either way, but not always the SAME finding: three of
#: the four were byte-identical bodies and were deleted, and the fourth was one
#: name over two contracts, closed by renaming the other contract rather than by
#: deleting either function. `_SECOND_READ_LAYER` records why it is empty. The
#: tuple still names all four, because the guard's subject is the NEXT second
#: copy and not the ones already closed.
_READ_LAYER_SYMBOLS = ("_read_document", "_document_problem", "_load_json",
                       "_artifact_guard")


#: A shipped module declaring its own run-artifact lock domain beside this
#: module's, with the reason it is still there. fallout D-010.
#: EMPTY, and that is the state this table was written to reach. `foundry.py`
#: declared a module-top `threading.RLock()` / `threading.local()` pair of its
#: own over the same documents — verdicts.json has one writer in each — and the
#: two excluded on disk only because two independently typed spellings of the
#: lock filename agreed, one of them a bare ".lock" literal at
#: `_locked_document` and `write_document`. It now binds this
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
    beside the name-collision sweep: this module's `_ARTIFACT_LOCK` and the
    other module's differently-named lock were two names for one rule, and no
    sweep keyed on names would ever have paired them. That pair is gone
    (concern C-003) and the rationale is not: the next
    module to open a domain of its own will not be named after this one either.
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


# --------------------------------------------------------------------------- #
# Hoisted for the layering guard (concern C-060)
# --------------------------------------------------------------------------- #
#
# Two readers moved here because verifier modules were reaching them through
# lifecycle modules, which is `fallout GI-033`'s violation column. Each is
# pinned twice: on its BEHAVIOUR, which survives the deletion of the definition
# it was copied from, and — for as long as both exist — on AGREEMENT with that
# definition, which is what makes the window between this commit and casting 2's
# repoint cost nothing. The agreement pins retire themselves with a named skip
# the day the originals go, rather than turning red for having succeeded.


def test_the_leaf_counts_the_requirement_ids_its_own_ladder_found(run_env):
    """`fallout GI-033` — the count reads the ladder that is already here.

    Three distinct ids, one of them quoted a second time mid-prose: the count is
    of the SET the ladder returns, so a repeat is not a fourth requirement. The
    ids come from `_spec_requirement_ids`, so which families count is
    `schemas.vocab`'s declaration and not a literal in either module.
    """
    project_root, fdir = run_env
    (fdir / "spec.md").write_text(
        "- **US-001** the story\n"
        "- **FR-009** the requirement\n"
        "- **AC-042** the criterion, which US-001 also names\n",
        encoding="utf-8",
    )

    assert artifacts.count_spec_requirements(project_root) == 3


def test_a_run_with_no_spec_counts_no_requirements(run_env):
    """A run legitimately has no spec before F0, and "none declared" is what is
    true of it — the ladder answers that without raising and so does the count.
    """
    project_root, _fdir = run_env

    assert artifacts.count_spec_requirements(project_root) == 0


def test_the_count_agrees_with_the_definition_it_was_hoisted_from(run_env):
    """The window pin: while `orchestration/gates.py` still defines its own
    count, the two answers are one answer.

    It is the copy that makes a hoist safe to land before the repoint, and it
    is the only thing that would catch the two parting while both are live.
    """
    gates = pytest.importorskip("foundry_mcp.tools.orchestration.gates")
    hoisted_from = getattr(gates, "_count_spec_requirements", None)
    if hoisted_from is None:
        pytest.skip(
            "gates._count_spec_requirements is gone: the hoist completed and "
            "this agreement pin has nothing left to compare"
        )

    project_root, fdir = run_env
    (fdir / "spec.md").write_text(
        "- **US-001** a\n- **FR-009** b\n- **OT-038** c\n", encoding="utf-8"
    )

    assert artifacts.count_spec_requirements(project_root) == hoisted_from(project_root)


#: One malformed manifest per rung the shape declaration names, and the two
#: shapes that are FINE. Every string is the message the reader must produce;
#: the point of each is that it names the rung that failed rather than the
#: document, which is what a message reused one rung down cannot do.
MANIFEST_SHAPES = {
    "a-list": ([1, 2, 3], "manifest.json is not a JSON object — parsed as list"),
    "a-string": ("nope", "manifest.json is not a JSON object — parsed as str"),
    "castings-not-a-list": (
        {"castings": "nope"},
        "manifest.json.castings is not a list — parsed as str",
    ),
    "casting-not-an-object": (
        {"castings": [1]},
        "manifest.json.castings[0] is not a JSON object — parsed as int",
    ),
    "casting-null": (
        {"castings": [None]},
        "manifest.json.castings[0] is not a JSON object — parsed as NoneType",
    ),
    "empty": ({}, None),
    "usable": ({"castings": [{"id": 1, "key_files": ["a.py"]}]}, None),
}


@pytest.mark.parametrize("shape", sorted(MANIFEST_SHAPES))
def test_the_manifest_rule_names_the_rung_that_failed(shape):
    """`fallout GI-033` — the predicate a verifier module may now ask directly.

    The rung, not the document: a refusal reading "manifest.json is not a JSON
    object — parsed as dict" sends an operator to look at a top-level object
    that is perfectly fine, which is why each branch states the shape IT
    expected and carries the dotted path down.
    """
    manifest, expected = MANIFEST_SHAPES[shape]

    assert artifacts.manifest_shape_problem(manifest) == expected


def test_a_record_missing_the_rung_its_readers_address_it_by_is_named():
    """The absent-key branch has no "parsed as" to give, so it gives the keys
    the object DOES carry — which is the actionable half when the rung a reader
    indexes by is the one that is missing.
    """
    problem = artifacts.manifest_shape_problem({"castings": [{"key_files": []}]})

    assert problem is not None
    assert problem.startswith("manifest.json.castings[0].id is absent or null")
    assert "['key_files']" in problem


def test_the_manifest_rule_agrees_with_the_definition_it_was_hoisted_from():
    """The other window pin, and the same retirement.

    Every shape above, through both definitions, while both exist: the copy is
    the original's body under a different spelling, and the only spellings that
    differ are the ones the single-definition guard forced apart.
    """
    spawn = pytest.importorskip("foundry_mcp.tools.foundry_spawn")
    hoisted_from = getattr(spawn, "_manifest_shape_problem", None)
    if hoisted_from is None:
        pytest.skip(
            "foundry_spawn._manifest_shape_problem is gone: the hoist completed "
            "and this agreement pin has nothing left to compare"
        )

    for name, (manifest, _expected) in sorted(MANIFEST_SHAPES.items()):
        assert artifacts.manifest_shape_problem(manifest) == hoisted_from(manifest), name


def test_every_manifest_key_the_declarations_readers_index_is_declared():
    """The declaration keeps its pin when the definition it came from goes.

    `foundry_spawn._MANIFEST_SHAPE` is pinned by
    `tests/test_spawn_progress.py::test_every_manifest_key_the_module_indexes_is_declared`,
    which derives the indexed key paths from THAT module's AST — so a reader
    there cannot start indexing a rung without declaring it. Concern C-060 put
    a copy of the declaration in this leaf for the verifier modules that may
    not reach a lifecycle one, and a copy with no pin is a table free to rot.

    THE SUBJECT IS THE READERS, NOT THIS FILE, and the difference is the whole
    design of the pin. `artifacts.py` indexes no manifest key at all — measured,
    not assumed: the derivation below returns the empty set for it — so a pin
    scoped to this module's own AST would assert nothing and pass forever,
    which is the failure mode every derived-membership check in this package
    warns about. What the declaration must cover is what its READERS index, and
    they live in the modules that call the predicate. `foundry_spawn` is that
    module today and stays one after the repoint, so it is the subject here.

    ONE DERIVATION, REACHED RATHER THAN COPIED. The taint-following scan lives
    in `tests/test_spawn_progress.py`; importing it is this suite's house
    pattern for a shared derivation and is what keeps the two pins measuring
    the same thing rather than agreeing by convention.
    """
    from tests.test_spawn_progress import _manifest_paths_in

    leaf_source = Path(artifacts.__file__).read_text(encoding="utf-8")
    assert _manifest_paths_in(leaf_source) == set(), (
        "the leaf has started indexing manifest records itself; this pin's "
        "subject must widen to include it rather than stay on its readers"
    )

    spawn = pytest.importorskip("foundry_mcp.tools.foundry_spawn")
    reader_source = Path(spawn.__file__).read_text(encoding="utf-8")
    paths = _manifest_paths_in(reader_source)

    # The scan must SEE something, or the assertion below passes vacuously.
    assert {"castings[].id", "waves[].wave"} <= paths, sorted(paths)

    undeclared = sorted(p for p in paths if not _declaration_covers(p))
    assert not undeclared, (
        f"{undeclared} are manifest key paths a reader of "
        f"`manifest_shape_problem` indexes but `_MANIFEST_DOCUMENT_SHAPE` does "
        f"not declare. Every such path is a rung the shared predicate does not "
        f"check and every reader therefore indexes unguarded. Declare the rung, "
        f"or declare it None to state on the record that the reader guards "
        f"itself. Do not delete the path from this assertion."
    )


def _declaration_covers(path: str) -> bool:
    """True when `_MANIFEST_DOCUMENT_SHAPE` declares `path` or frees it.

    "Frees" is the `None` element shape: reaching one means the table has
    STATED that the reader below it is on its own, which is a decision someone
    made rather than a rung nobody thought about.
    """
    shape: object = artifacts._MANIFEST_DOCUMENT_SHAPE
    for token in re.findall(r"\[\]|[^.\[\]]+", path):
        if shape is None:
            return True
        if token == "[]":
            if not isinstance(shape, list):
                return False
            shape = shape[0]
            continue
        if not isinstance(shape, dict) or token not in shape:
            return False
        shape = shape[token]
    return True


# --------------------------------------------------------------------------- #
# C-060 row 2: the DONE gate's report read
# --------------------------------------------------------------------------- #


def _complete_report(run_dir: Path, *, markdown: str | None = None) -> Path:
    """A run directory holding a report both documents call complete."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "report.json").write_text(
        json.dumps({k: [] for k in REPORT_REQUIRED_SECTIONS} | {"generated_at": "t"}),
        encoding="utf-8",
    )
    (run_dir / "REPORT.md").write_text(
        markdown
        if markdown is not None
        else "\n".join(f"## {REPORT_SECTION_TITLES[k]}" for k in REPORT_REQUIRED_SECTIONS),
        encoding="utf-8",
    )
    return run_dir


def test_the_headings_this_read_looks_for_are_the_ones_the_seal_writes(tmp_path):
    """`fallout GI-033` — the row that could only be hoisted once ONE table existed.

    This read checks headings the report SEAL wrote. While the table lived in
    the presentation module, a leaf copy of it would have given the writer one
    table and the checker another: rename a heading and the gate reports a
    section missing that the seal had just written — a gate refusing a run for
    a document it produced correctly. So the assertion is identity, not
    equality. A copy that happened to agree today would pass an equality check
    and is exactly what must not exist.
    """
    assert artifacts.REPORT_SECTION_TITLES is vocab.REPORT_SECTION_TITLES


def test_the_done_gates_read_finds_a_complete_report(tmp_path):
    """Both documents carry every section, so nothing is missing and the read
    carries the generation stamp a caller reports.
    """
    status = artifacts.report_document_status(_complete_report(tmp_path / "run"))

    assert status["present"] is True
    assert status["missing_sections"] == []
    assert status["generated_at"] == "t"


def test_a_report_with_no_markdown_is_not_present_however_complete_the_json(
    tmp_path,
):
    """D-015 driven, and it is the reason this reads two documents.

    Delete REPORT.md outright and the JSON still answers every section — the
    read that trusted the JSON alone passed here, so a run could reach DONE
    with no operator-readable report at all, which is the outcome
    `convergence GI-006` exists to prevent.
    """
    run_dir = _complete_report(tmp_path / "run")
    (run_dir / "REPORT.md").unlink()

    status = artifacts.report_document_status(run_dir)

    assert status["present"] is False
    assert status["missing_sections"] == list(REPORT_REQUIRED_SECTIONS)
    assert status["missing_from_json"] == []
    assert "REPORT.md does not exist" in status["problem"]


def test_prose_the_lead_appended_hides_no_generated_section(tmp_path):
    """`convergence GI-006` licenses the lead to APPEND, so a document with its own
    sections above, below and between the generated ones is still complete —
    the heading is found at any depth and in any order.
    """
    generated = [f"## {REPORT_SECTION_TITLES[k]}" for k in REPORT_REQUIRED_SECTIONS]
    markdown = "\n".join(
        ["## Lead preface", "some prose", *generated, "## Appendix", "more prose"]
    )

    status = artifacts.report_document_status(
        _complete_report(tmp_path / "run", markdown=markdown)
    )

    assert status["present"] is True
    assert status["missing_from_markdown"] == []


def test_a_generated_heading_with_a_suffix_reads_as_the_edit_it_is(tmp_path):
    """The match is the whole trimmed line, not a prefix. An edited heading is
    an edit, and the lead's own `## Appendix` is not a generated section.
    """
    edited = REPORT_REQUIRED_SECTIONS[0]
    markdown = "\n".join(
        f"## {REPORT_SECTION_TITLES[k]}" + (" (see below)" if k == edited else "")
        for k in REPORT_REQUIRED_SECTIONS
    )

    status = artifacts.report_document_status(
        _complete_report(tmp_path / "run", markdown=markdown)
    )

    assert status["present"] is False
    assert status["missing_sections"] == [edited]
    assert status["missing_from_json"] == []


def test_the_report_read_agrees_with_the_definition_it_was_hoisted_from(tmp_path):
    """The window pin for row 2, retiring itself the same way rows 1 and 3 do.

    Four states, through both definitions while both exist: complete, an empty
    pair of documents, a corrupt JSON, and a directory with no report at all.
    The payloads must be equal and not merely agree on `present` — the callers
    read `missing_sections`, `problem` and both halves of the union.
    """
    report = pytest.importorskip("foundry_mcp.tools.foundry_report")
    hoisted_from = getattr(report, "report_status", None)
    if hoisted_from is None:
        pytest.skip(
            "foundry_report.report_status is gone: the hoist completed and this "
            "agreement pin has nothing left to compare"
        )

    empty = tmp_path / "empty"
    empty.mkdir()
    blank = tmp_path / "blank"
    blank.mkdir()
    (blank / "report.json").write_text("{}", encoding="utf-8")
    (blank / "REPORT.md").write_text("# nothing", encoding="utf-8")
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "report.json").write_text("not json{", encoding="utf-8")

    for run_dir in (_complete_report(tmp_path / "run"), empty, blank, corrupt):
        assert artifacts.report_document_status(run_dir) == hoisted_from(run_dir), run_dir


# --------------------------------------------------------------------------- #
# fallout D-123 — THE APPEND-ONLY HALF OF THE LOCK DOMAIN
#
# `_document_transaction` is the read-modify-write for a JSON document. A run
# also holds APPEND-ONLY artifacts — `handoffs.jsonl` and its `handoffs.md`
# mirror — and until this cycle there was no locked primitive for them at all,
# so `foundry_handoff._append_handoff_record` was the one run-artifact writer in
# the package holding no lock: four to six separate `f.write` calls into the
# human mirror, plus a header bootstrap on a bare `not md_path.exists()`, in a
# tree several teammates share and a function fallout GI-003 gave a second
# writer.
#
# The tests below drive the primitive and then assert the writer takes it,
# because either one alone is half a guard: a lock nobody holds excludes
# nothing, and a `with` block over a primitive that does not really exclude is
# a comment.
# --------------------------------------------------------------------------- #


def test_the_append_lock_is_the_document_name_plus_the_declared_suffix(run_env):
    """One spelling for both primitives, or they exclude by coincidence.

    The whole reason `_TX_LOCK_SUFFIX` is a constant is that two independently
    typed spellings of a lock filename order two writers only until one of them
    is edited (fallout D-010). A second primitive that built its own name would
    reopen exactly that.
    """
    _project_root, fdir = run_env
    ledger = fdir / "handoffs.jsonl"

    with _artifact_lock(ledger):
        present = {p.name for p in fdir.iterdir()}

    assert f"handoffs.jsonl{_TX_LOCK_SUFFIX}" in present, sorted(present)


def test_the_append_lock_excludes_a_second_thread_until_it_is_released(run_env):
    """The property the whole fix rests on, driven rather than assumed."""
    _project_root, fdir = run_env
    ledger = fdir / "handoffs.jsonl"

    holder_inside = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def hold():
        with _artifact_lock(ledger):
            holder_inside.set()
            release.wait(5)

    def contend():
        with _artifact_lock(ledger):
            second_entered.set()

    holder = threading.Thread(target=hold)
    holder.start()
    assert holder_inside.wait(5), "the holder never entered its critical section"

    other = threading.Thread(target=contend)
    other.start()
    # While the first thread holds it, the second must not get in.
    assert not second_entered.wait(0.3), "the lock let a second thread through"

    release.set()
    other.join(5)
    holder.join(5)
    assert second_entered.is_set(), "the lock never released"


def test_a_nested_append_lock_on_one_path_does_not_block_on_itself(run_env):
    """Re-entrancy per path, against BOTH primitives.

    `flock` is held per open file description, so a second acquire on the same
    path from the same thread would block on a lock that thread will not release
    until the outer block exits — the deadlock fallout D-010 records as "one
    call away from reachable". Asserted for a nested lock and for a lock taken
    inside an open document transaction on the same path.
    """
    _project_root, fdir = run_env
    ledger = fdir / "handoffs.jsonl"

    reached = []
    with _artifact_lock(ledger):
        with _artifact_lock(ledger):
            reached.append("nested")
    assert reached == ["nested"]

    state = fdir / "state.json"
    _save_json(state, {"phase": "F0"})
    with _document_transaction(state) as doc:
        doc["phase"] = "F1"
        with _artifact_lock(state):
            reached.append("inside a transaction")
    assert reached == ["nested", "inside a transaction"]
    # And the transaction still wrote what it mutated.
    assert _load_json(state)["phase"] == "F1"


def test_the_handoff_ledger_writes_both_channels_under_one_lock(run_env):
    """fallout D-123 — the writer takes it, and takes ONE for the record.

    A handoff record spans two files. Locking each file separately would order
    each write and still let two records interleave BETWEEN the channels, which
    is the failure the lock is taken against — so the assertion is that both
    channel writes sit inside a single `_artifact_lock` block, not merely that
    the name appears in the function.

    Read off the source: what is being asserted is the STRUCTURE of the
    critical section, and a behavioural test for an interleaving is a race
    either way it comes out.
    """
    handoff = pytest.importorskip("foundry_mcp.tools.foundry_handoff")
    tree = ast.parse(Path(handoff.__file__).read_text(encoding="utf-8"))
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_append_handoff_record"
    )

    locks = [
        node
        for node in ast.walk(fn)
        if isinstance(node, ast.With)
        and any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "_artifact_lock"
            for item in node.items
        )
    ]
    assert len(locks) == 1, (
        f"{len(locks)} _artifact_lock block(s) in _append_handoff_record; the "
        "record spans two channels and takes ONE lock across both"
    )

    opened = {
        node.func.value.id
        for node in ast.walk(locks[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "open"
        and isinstance(node.func.value, ast.Name)
    }
    assert opened == {"jsonl_path", "md_path"}, sorted(opened)


def test_the_ledger_writer_holds_no_lock_domain_of_its_own(run_env):
    """The fix is a shared domain, not a second one.

    `_declares_lock_domain` above is the guard for a module opening its own
    `threading.RLock()` / `threading.local()` pair; this is the same rule asked
    of the module the fix landed in, from the other end — it binds the leaf's
    lock and spells the sidecar through the leaf's suffix, or it excludes
    nothing that matters.
    """
    handoff = pytest.importorskip("foundry_mcp.tools.foundry_handoff")
    path = Path(handoff.__file__)
    assert not _declares_lock_domain(path)
    assert handoff._artifact_lock is _artifact_lock


# --------------------------------------------------------------------------- #
# fallout D-128 — THE EVIDENCE ENGINE IS REACHED THROUGH NAMED SEAMS ONLY
#
# fallout GI-033's violation column names "any lifecycle module importing a verifier
# module", and `tools/evidence.py` is a decider by `VERIFIER_PATH_PATTERNS` —
# "the module that re-executes it decides whether a log passes". Two modules
# reach it, one from each layer, and BOTH do so through a call-time import: the
# boundary guard in `tests/orchestration/` measures module-top edges by design
# ("a function-local import is NOT an edge here, and that is the whole point of
# the lazy seam"), so neither reacher was visible to any layering assertion.
#
# The roster is pinned here rather than left implicit. A THIRD reacher, or the
# promotion of either of these to module top, is a layering decision someone has
# to make on purpose.
# --------------------------------------------------------------------------- #

#: (module basename, function) for every call-time reach into the evidence
#: engine, with what each one is. Not an allowlist: the assertion is equality,
#: so a row that stops accounting for anything fails exactly as a new reacher
#: does.
_EVIDENCE_ENGINE_SEAMS = {
    # LIFECYCLE. The acceptance gate re-executes a casting's evidence corpus
    # before it will accept the casting (fallout CT-015 / FR-010). Lazy because
    # `evidence.py` imports `declared_requirement_ids` from `foundry_handoff` at
    # module top, so promoting this closes a cycle and the package stops
    # loading.
    ("foundry_handoff", "foundry_accept_casting"),
    # VERIFIER. The boundary sweep, a rung of three preconditions functions.
    ("evidence_boundary", "_sweep_evidence_at_boundary"),
}


def test_the_evidence_engine_is_reached_by_the_named_seams_only():
    """fallout D-128 / fallout GI-033 — the edge exists; what it may not be is unseen."""
    module_top: list[str] = []
    lazy: set[tuple[str, str]] = set()

    for path in _shipped_modules():
        if path.stem == "evidence":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "foundry_mcp.tools.evidence":
                    module_top.append(path.stem)
            elif isinstance(node, ast.Import):
                module_top.extend(
                    path.stem
                    for a in node.names
                    if a.name == "foundry_mcp.tools.evidence"
                )
        for parent in ast.walk(tree):
            if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(parent):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module == "foundry_mcp.tools.evidence":
                        lazy.add((path.stem, parent.name))
                elif isinstance(node, ast.Import):
                    lazy |= {
                        (path.stem, parent.name)
                        for a in node.names
                        if a.name == "foundry_mcp.tools.evidence"
                    }

    assert module_top == [], (
        f"module(s) importing the evidence engine AT MODULE TOP: {module_top}. "
        "`evidence.py` imports `foundry_handoff` at module top, so a load-time "
        "edge back into it stops the package loading — and a lifecycle module "
        "reaching a decider at load time is GI-033's violation column besides."
    )
    assert lazy == _EVIDENCE_ENGINE_SEAMS, (
        f"the evidence-engine seam roster moved: {sorted(lazy)}. Each seam is a "
        "lifecycle-or-verifier module reaching the module that decides whether "
        "an evidence log passes; add the row with what it is and why it must be "
        "lazy, or take the row with the edge."
    )


def test_the_published_digest_spelling_has_one_home(run_env):
    """fallout D-128 — the pair moved to the leaf, and it moved TOGETHER.

    Both layers read these: `foundry_handoff` and `foundry_validate` from the
    lifecycle side, `tools/evidence.py` from the verifier side. fallout GI-033's
    arithmetic gives one home for a symbol read from both, and it is a leaf.
    The pair is one rule with two arities — each docstring names the other as
    the wrong choice for the other's input, and D-108 is the defect that
    pairing prevents — so a split home would leave each warning pointing at a
    module that no longer holds the thing it warns about.
    """
    _project_root, fdir = run_env
    leaf = Path(artifacts.__file__).resolve()
    assert {"_hash_file", "_hash_str"} <= _top_level_bindings(leaf)

    elsewhere = sorted(
        path.name
        for path in _shipped_modules()
        if path != leaf and {"_hash_file", "_hash_str"} & _top_level_bindings(path)
    )
    assert elsewhere == [], (
        f"module(s) keeping a second copy of the digest spelling: {elsewhere}"
    )

    # The bodies are the bodies, not a rewrite: bytes for a file, encoded text
    # for a string, and the published 16-character prefix on both.
    doc = fdir / "state.json"
    doc.write_bytes(b"payload\r\n")
    assert _hash_file(doc) == _hash_str("payload\r\n")
    assert _hash_file(doc).startswith("sha256:")
    assert len(_hash_file(doc)) == len("sha256:") + 16
    assert _hash_file(fdir / "absent.json") is None
