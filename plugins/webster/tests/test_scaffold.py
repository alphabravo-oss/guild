"""Tests for plugins/webster/scripts/scaffold.py (spec US-009).

Every test drives the script through the ``run_script`` conftest helper
(GI-004, CT-007) against a ``tmp_path`` docs directory, so ``init`` never
writes into the repository and ``check`` never reads it.

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
- ``test_help_names_an_exit_set_for_both_modes`` — FR-028 / CT-005. RED on the
  pre-change script: the module docstring is argparse's ``description``, so it
  *is* ``scaffold.py --help``, and it stated an exit contract for ``check``
  alone ("exit 1 on any violation") while ``init`` had grown two exit-2
  envelopes whose whole purpose is to be distinguishable from that 1. A caller
  reading exit codes had no published place to learn them.
- ``test_unreadable_page_exits_two_without_a_traceback`` — FR-028 / CT-005.
  RED on the pre-change script: ``do_check`` read every page through a bare
  ``open()``, so one dangling symlink under docs/ raised ``FileNotFoundError``
  out of ``main()`` — a traceback on stderr, nothing on stdout, and exit 1,
  which this script reserves for a real layout violation. It now reports
  ``cannot_read``. The same test runs ``check`` on the same tree before
  planting the symlink, so the exit 2 cannot come from a tree that was already
  failing.
- ``test_malformed_category_json_stays_a_violation`` — FR-028 / CT-005. Green
  before and after by design: the ``_category_.json`` handler was narrowed from
  ``except Exception`` to the three families a malformed file raises, so that
  an OSError reaches the ``cannot_read`` boundary instead of being filed as a
  violation. This pins the half that must not move.
- ``test_every_documented_envelope_leads_with_status`` — FR-028 / CT-005. RED on
  the script as it stood: the docstring claimed "every one of those is a JSON
  object on stdout whose first key is ``status``", and ``init``'s exit-0
  envelope led with ``created`` and had no ``status`` key at all, so the one
  assertion that reads the claim back — first key, then value, on each of the
  seven envelopes the docstring enumerates — raised ``KeyError: 'status'`` on
  the ordinary success path. The other tests here each check one envelope in
  depth and so could not see that the *set* was described wrongly. This is the
  test that reads the published contract back off the script; the envelopes it
  reaches are the six a non-root process can provoke, and ``cannot_read``'s
  unlistable-directory half is left to the page half, because the only fixture
  for it is a mode bit root reads straight through.

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


# -----------------------------------------------------------------------------
# FR-028 / CT-005: the exit contract is published where a caller can read it.
# -----------------------------------------------------------------------------
def test_help_names_an_exit_set_for_both_modes(run_script):
    result = run_script("scaffold.py", "--help")

    assert result.returncode == 0, (
        f"Expected --help to exit 0; got {result.returncode}" + outcome(result)
    )
    help_text = result.stdout
    assert "  init " in help_text and "  check " in help_text, (
        "Expected --help to describe both modes; the module docstring is "
        "argparse's description, so this is where the contract is published"
        + outcome(result)
    )
    init_block = help_text.split("  init ", 1)[1].split("  check ", 1)[0]
    check_block = help_text.split("  check ", 1)[1]

    # init is the mode that grew the exit-2 envelopes, and it was the one the
    # docstring said nothing about. Naming exit 2 only under check would leave a
    # caller reading `bad_subject` with no published reason it is not a 1.
    assert "exit 2" in init_block, (
        "Expected --help to name exit 2 for init: a bad key and a refused write "
        "are could-not-run, and the whole point of the code is that it is not "
        "check's violation 1" + outcome(result)
    )
    for status in ("bad_subject", "cannot_write"):
        assert status in init_block, (
            f"Expected --help to name init's {status} status; an exit code with "
            f"no published status leaves a caller parsing prose" + outcome(result)
        )
    assert "exit 1" in check_block and "exit 2" in check_block, (
        "Expected --help to keep check's exit 1 and name its exit 2"
        + outcome(result)
    )
    for status in ("no_docs", "cannot_read"):
        assert status in check_block, (
            f"Expected --help to name check's {status} status" + outcome(result)
        )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: a page that cannot be read is a could-not-check, not a
# layout violation.
# -----------------------------------------------------------------------------
def test_unreadable_page_exits_two_without_a_traceback(run_script, tmp_path):
    docs = tmp_path / "docs"
    built = run_script("scaffold.py", "init", "--docs", docs)
    assert built.returncode == 0, (
        f"The tree this test breaks has to be built first; init gave "
        f"{built.returncode}" + outcome(built)
    )

    # The control runs first and on the same tree, so the exit 2 below can only
    # come from the symlink. A separate happy-path test would not prove that:
    # scaffold check reports on a whole tree, and one already-failing page in it
    # would produce the same non-zero code for an unrelated reason.
    clean = run_script("scaffold.py", "check", "--docs", docs)
    assert clean.returncode == 0, (
        f"Expected the freshly scaffolded tree to pass its own check before the "
        f"symlink is planted; got {clean.returncode}" + outcome(clean)
    )

    # A dangling symlink, not `chmod 000`: root reads straight through a mode
    # bit, so a permissions fixture passes by not running, which is the failure
    # shape this suite exists to catch. A broken link is refused for everyone.
    broken = docs / "install" / "broken.md"
    broken.symlink_to("nowhere.md")

    result = run_script("scaffold.py", "check", "--docs", docs)

    assert result.returncode == 2, (
        f"Expected exit 2 (nothing to check): a page os.listdir names and "
        f"open() refuses is not a layout violation, and exit 1 is reserved for "
        f"those; got {result.returncode}" + outcome(result)
    )
    assert "Traceback" not in result.stderr, (
        "An unreadable page must be reported, not raised: the traceback exits 1 "
        "with an empty stdout, so a caller reading JSON sees no envelope at all"
        + outcome(result)
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "cannot_read", (
        "Expected a cannot_read JSON envelope on stdout" + outcome(result)
    )
    assert payload["path"] == str(broken), (
        f"Expected the unreadable page named in the envelope; got "
        f"{payload.get('path')!r}" + outcome(result)
    )
    assert payload.get("error"), (
        "Expected the envelope to carry what the filesystem said" + outcome(result)
    )
    assert "violations" not in payload, (
        "A scan that stopped part way through has not earned a findings list: "
        "reported as `violations` it is a partial answer read as a whole one"
        + outcome(result)
    )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: narrowing the _category_.json handler must not reclassify
# the malformed file it was written for.
# -----------------------------------------------------------------------------
def test_malformed_category_json_stays_a_violation(run_script, tmp_path):
    docs = tmp_path / "docs"
    built = run_script("scaffold.py", "init", "--docs", docs)
    assert built.returncode == 0, (
        f"The tree this test breaks has to be built first; init gave "
        f"{built.returncode}" + outcome(built)
    )
    category = docs / "install" / "_category_.json"

    # One shape per family the narrowed handler still catches: bytes that are
    # not JSON (ValueError), a top-level array, which has no .get
    # (AttributeError), and a position that cannot be a dict key (TypeError).
    # `except Exception` caught all three and OSError with them; only the OSError
    # was meant to move.
    for body in ('not json at all\n', '[1, 2]\n', '{"position": [1]}\n'):
        category.write_text(body, encoding="utf-8")

        result = run_script("scaffold.py", "check", "--docs", docs)

        assert result.returncode == 1, (
            f"Expected exit 1 for _category_.json holding {body!r}: Docusaurus "
            f"cannot order a sidebar from it, which is a layout violation, not a "
            f"file that could not be read; got {result.returncode}"
            + outcome(result)
        )
        assert "Traceback" not in result.stderr, (
            f"_category_.json holding {body!r} must be reported, not raised"
            + outcome(result)
        )
        payload = json.loads(result.stdout)
        assert payload["status"] == "violations", (
            f"Expected a violations envelope for {body!r}; got "
            f"{payload.get('status')!r}" + outcome(result)
        )
        problems = [v["problem"] for v in payload["violations"]]
        assert "unreadable _category_.json" in problems, (
            f"Expected the malformed file named as the violation for {body!r}; "
            f"got {problems}" + outcome(result)
        )


# -----------------------------------------------------------------------------
# FR-028 / CT-005: the docstring enumerates seven envelopes and claims one shape
# for all of them. This is the assertion that reads that claim back.
# -----------------------------------------------------------------------------
def test_every_documented_envelope_leads_with_status(run_script, tmp_path):
    docs = tmp_path / "docs"
    not_a_directory = tmp_path / "regular-file"
    not_a_directory.write_text("a file, not a directory\n", encoding="utf-8")

    built = run_script("scaffold.py", "init", "--docs", docs)
    clean = run_script("scaffold.py", "check", "--docs", docs)

    # cannot_read is provoked before the tree is broken and the link is pulled
    # straight back out, so the violations run below reports the missing root
    # page rather than a page the boundary refused to open.
    broken = docs / "install" / "broken.md"
    broken.symlink_to("nowhere.md")
    unreadable = run_script("scaffold.py", "check", "--docs", docs)
    broken.unlink()

    (docs / "index.md").unlink()
    violations = run_script("scaffold.py", "check", "--docs", docs)

    documented = [
        ("init", 0, "ok", built),
        ("init", 2, "bad_subject",
         run_script("scaffold.py", "init", "--docs", tmp_path / "typo",
                    "--subject", "Bad Key:Label")),
        ("init", 2, "cannot_write",
         run_script("scaffold.py", "init", "--docs", not_a_directory)),
        ("check", 0, "ok", clean),
        ("check", 1, "violations", violations),
        ("check", 2, "no_docs",
         run_script("scaffold.py", "check", "--docs", tmp_path / "nowhere")),
        ("check", 2, "cannot_read", unreadable),
    ]

    for mode, code, status, result in documented:
        assert result.returncode == code, (
            f"The docstring puts {mode}'s {status} envelope at exit {code}; got "
            f"{result.returncode}" + outcome(result)
        )
        payload = json.loads(result.stdout)
        leading = next(iter(payload))
        # json.loads preserves document order, so the first key of the dict is
        # the first key a reader sees. The docstring promises it is `status` on
        # every one of these, which is what lets a caller read a single field to
        # tell a refused write from a bad key from a tree that is simply fine.
        assert leading == "status", (
            f"The docstring says every envelope leads with `status`; {mode}'s "
            f"{status} envelope leads with {leading!r}" + outcome(result)
        )
        assert payload["status"] == status, (
            f"Expected {mode} to report status {status!r} at exit {code}; got "
            f"{payload['status']!r}" + outcome(result)
        )
