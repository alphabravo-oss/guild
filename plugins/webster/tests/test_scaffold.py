"""Tests for plugins/webster/scripts/scaffold.py (spec US-009).

Both tests drive the script through the ``run_script`` conftest helper
(GI-004, CT-007) against a ``tmp_path`` docs directory, so ``init`` never
writes into the repository.

Which fix each test pins, and what it did before the fix (FR-039, AC-039 — the
red-first property is recorded here rather than enforced at runtime):

- ``test_bad_subject_key_writes_nothing_and_exits_two`` — FR-028 / OT-034 /
  CT-005. RED on the pre-change script: ``do_init`` called ``parse_subjects``
  only after ``stub`` had already put index.md, faq.md and the ``FRONT``
  sections on disk, and a bad key left ``parse_subjects`` through
  ``sys.exit("subject key ...")`` — stderr text, empty stdout, and exit 1, the
  same code ``check`` uses for a real layout violation. ``parse_subjects`` now
  raises ``BadSubject`` and ``do_init`` calls it above the first write.
- ``test_valid_subject_key_creates_the_landing_page`` — FR-028 / OT-035.
  Green before and after by design: hoisting the validation must not change
  what a good key produces.
- ``test_unwritable_docs_path_exits_two_without_a_traceback`` — FR-028 /
  CT-005. RED on the pre-change script: ``do_init`` called ``os.makedirs``
  unguarded, so a ``--docs`` naming a regular file raised ``FileExistsError``
  out of ``main()`` — a traceback on stderr, an empty stdout where the envelope
  belongs, and exit 1, which CT-005 reserves for a layout violation. It now
  reports ``cannot_write``. Its control is the test above, which runs the same
  argv with only ``--docs`` changed and still reaches exit 0 and
  clusters/clusters.md; the pair isolates the docs target as the one variable,
  which is why no third copy of the happy path was added here.

No test uses ``@pytest.mark.skip`` or ``xfail`` (A-029).
"""

from __future__ import annotations

import json


def outcome(result) -> str:
    """The message every assertion below appends (CT-007)."""
    return f"\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"


# -----------------------------------------------------------------------------
# FR-028 / OT-034 / AC-036 / CT-005: validate before the first write.
# -----------------------------------------------------------------------------
def test_bad_subject_key_writes_nothing_and_exits_two(run_script, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()

    result = run_script(
        "scaffold.py", "init", "--docs", docs, "--subject", "Bad Key:Label"
    )

    assert result.returncode == 2, (
        f"Expected exit 2 (could not run): a bad subject key is not a layout "
        f"violation, and exit 1 is reserved for those; got {result.returncode}"
        + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "bad_subject", (
        "Expected a bad_subject JSON envelope on stdout, not a bare error line"
        + outcome(result)
    )
    assert payload["subject"] == "Bad Key", (
        "Expected the offending key named in the envelope" + outcome(result)
    )
    assert payload.get("error"), (
        "Expected the envelope to say what is wrong with the key"
        + outcome(result)
    )
    left_behind = sorted(p.name for p in docs.iterdir())
    assert left_behind == [], (
        f"Expected nothing written: a typo must not leave a half-built tree. "
        f"Found {left_behind}" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / OT-035 / AC-037: a good key still scaffolds its subject.
# -----------------------------------------------------------------------------
def test_valid_subject_key_creates_the_landing_page(run_script, tmp_path):
    docs = tmp_path / "docs"

    result = run_script(
        "scaffold.py", "init", "--docs", docs, "--subject", "clusters:Clusters"
    )

    assert result.returncode == 0, (
        f"Expected exit 0 for a well-formed subject key; got "
        f"{result.returncode}" + outcome(result)
    )
    landing = docs / "clusters" / "clusters.md"
    assert landing.is_file(), (
        f"Expected the subject landing page at {landing}" + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["subjects"] == ["clusters"], (
        "Expected the parsed subject reported back in the init envelope"
        + outcome(result)
    )
    assert "clusters/clusters.md" in payload["created"], (
        "Expected the landing page listed as created" + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: a write the filesystem refuses is a could-not-run, not a
# layout violation.
# -----------------------------------------------------------------------------
def test_unwritable_docs_path_exits_two_without_a_traceback(run_script, tmp_path):
    # A regular file where the docs directory should be. Chosen over `chmod 000`
    # because root writes through a mode bit and would turn this into a test that
    # passes by not running, which is the failure shape this suite exists to catch.
    docs = tmp_path / "docs"
    docs.write_text("a file, not a directory\n", encoding="utf-8")

    result = run_script(
        "scaffold.py", "init", "--docs", docs, "--subject", "clusters:Clusters"
    )

    assert result.returncode == 2, (
        f"Expected exit 2 (could not run): the filesystem refused the write, "
        f"which is not the layout violation exit 1 means; got "
        f"{result.returncode}" + outcome(result)
    )
    assert "Traceback" not in result.stderr, (
        "A refused write must be reported, not raised: the traceback exits 1 "
        "with an empty stdout, so a caller reading JSON sees no envelope at all"
        + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "cannot_write", (
        "Expected a cannot_write JSON envelope on stdout" + outcome(result)
    )
    assert payload["path"] == str(docs), (
        f"Expected the refused path named in the envelope; got "
        f"{payload.get('path')!r}" + outcome(result)
    )
    assert payload.get("error"), (
        "Expected the envelope to carry what the filesystem said" + outcome(result)
    )
    assert payload["created"] == [], (
        f"Expected nothing created: the first makedirs failed, so no page was "
        f"written. Envelope claims {payload['created']}" + outcome(result)
    )
    assert docs.read_text(encoding="utf-8") == "a file, not a directory\n", (
        "The refused target must be left exactly as it was found" + outcome(result)
    )
