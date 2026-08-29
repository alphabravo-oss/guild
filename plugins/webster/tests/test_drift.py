"""CLI tests for plugins/webster/scripts/drift.py (US-001, US-002, US-010).

Every test drives the script through the conftest ``run_script`` helper as a
subprocess (GI-004, CT-007). Nothing here imports drift.py: it resolves ROOT,
DOCS and MANIFEST at module import, so an import would freeze the wrong tree.

Which fix each test pins, and how it failed before it. No citation here names a
line number: ten revisions of drift.py landed between ``caa2e5c``, the commit
that wrote this module, and ``02f4e9e``, the one that ships it — read back from
``git log --oneline --reverse caa2e5c..02f4e9e --
plugins/webster/scripts/drift.py`` rather than remembered — and every line
number this module once cited had gone stale by the time anyone read it, so
functions and fields name the code instead. The version of this sentence that
said "rewritten three times" was written in 3ab01dd, the third of those ten,
and named no end, so it went stale the way the line numbers it explains had.
Both ends are named for that reason, and the commit that names them touches no
file under ``scripts/`` — which is what keeps the range closed rather than one
short again the moment it lands.
Every row was measured RED by running this module against ``git show
cfafe8e:plugins/webster/scripts/drift.py``, the pre-change baseline AC-039
names; "was at" is the revision the row's last column describes.

==================================================  =============  =======  =======================
test                                                pins           was at   was, before the fix
==================================================  =============  =======  =======================
test_uncommitted_edit_to_cited_file_is_drift        FR-001 AC-001  cfafe8e  status clean, exit 0
test_staged_rename_marks_old_and_new_path_dirty     FR-001 AC-002  cfafe8e  suspect_pages {}
test_docs_below_the_git_root_still_match_anchors    FR-001 US-001  f31b144  unrelated_changes, 0
test_diff_relative_config_still_reports_drift       FR-001 AC-001  944c958  status clean, exit 0
test_cited_file_outside_the_docs_root_is_drift      FR-005 US-001  693570d  status clean, exit 0
test_cited_file_inside_the_docs_tree_is_drift       FR-005 ST-001  693570d  status clean, exit 0
test_docs_tree_at_the_repo_root_reaches_clean       FR-001 FR-005  19941ad  unrelated_changes, 0
test_check_outside_a_git_repo_reports_no_git        FR-002 AC-003  cfafe8e  status drift, exit 1
test_rebased_away_head_reports_head_missing         FR-037 AC-004  cfafe8e  status clean, exit 0
test_non_ascii_path_under_a_c_locale_still_answers  FR-002 FR-037  9859317  traceback, no JSON
test_unparseable_manifest_reports_no_manifest       FR-006 CT-001  19c97f9  traceback, no JSON
test_wrong_typed_manifest_field_is_no_manifest      FR-006 CT-001  f31b144  traceback, no JSON
test_no_manifest_envelope_lists_broken_anchors      AC-003 CT-001  693570d  key named `broken`
test_record_writes_a_line_hash_per_anchor           FR-003 OT-005  cfafe8e  no lineHashes key
test_changed_cited_line_is_reported_as_a_mismatch   FR-003 AC-005  cfafe8e  no hash_mismatches key
test_hash_mismatch_alone_is_drift                   FR-007         cfafe8e  status clean, exit 0
test_manifest_without_line_hashes_is_not_recorded   FR-004 AC-006  cfafe8e  no hashes key
test_no_git_note_does_not_claim_digests_compared    FR-004 AC-006  66dab76  note claimed compared
test_partly_hashed_manifest_is_not_reported_clean   FR-003 FR-007  f31b144  status clean, exit 0
test_anchor_past_end_of_file_gets_no_line_hash      FR-034         cfafe8e  no lineHashes key
test_anchor_citing_line_zero_is_a_broken_anchor     FR-034 FR-003  19941ad  hashes_partial, exit 2
test_latin1_cited_line_byte_change_is_a_mismatch    FR-003 US-001  19c97f9  status clean, exit 0
test_uncited_commit_is_unrelated_changes            FR-005 AC-007  cfafe8e  status drift, exit 1
test_docs_only_edit_is_unrelated_changes            FR-008 AC-008  cfafe8e  status drift, exit 1
test_docs_edit_adding_a_citation_is_unrelated       FR-008 ST-003  19941ad  hashes_partial, exit 2
test_broken_anchor_outranks_unrelated_churn         FR-007 AC-009  cfafe8e  no hashes key
test_nothing_changed_since_record_is_clean          FR-005 AC-010  cfafe8e  no hashes key
test_docs_directory_with_no_pages_is_not_clean      FR-005 FR-006  19c97f9  status clean, exit 0
test_md_page_citing_a_changed_file_is_drift         FR-005 ST-001  19c97f9  green: the .mdx control
test_mdx_page_citing_a_changed_file_is_drift        FR-005 FR-006  19c97f9  unrelated_changes, 0
test_record_without_a_commit_writes_a_null_head     FR-009 OT-010  cfafe8e  gitHead "", no note
test_null_head_in_the_manifest_reports_no_git       FR-005 FR-009  9859317  status clean, exit 0
==================================================  =============  =======  =======================

Two of those tests pin a second fix each, and the table above carries one row
per test rather than one per fix. Both were RED again at a2b1e1d, where the
notes counted a population wider than the comparison loop had read:
``test_no_git_note_does_not_claim_digests_compared`` for the not-checked
sentence "every resolvable anchor ... was compared", said over a tree that had
grown a citation the record never held (ST-004, ST-005, FR-004), and
``test_partly_hashed_manifest_is_not_reported_clean`` for "1 of 3 resolvable
anchors" where 1 of the 2 the record held is what was measured (CT-001,
FR-004). A citation added after the record cannot be missing a digest the
record never took, so counting it in either number states the gap over a set
nothing compared.

The exit map under test is FR-006: clean and unrelated_changes 0, drift 1, and
no_docs / no_manifest / no_anchors / no_git / head_missing / hashes_partial 2.

No test uses ``@pytest.mark.skip`` or ``xfail``. A drift test that skips is the
same false pass the script is being fixed to stop reporting.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

# The one real anchor in the committed fixture: docs/items/create-item.md:12
# carries `<!-- src/app/main.py:15 -->`. Line 15 of the post-"touch main" file is
# blank, which is why the hash assertions below read the line rather than
# hard-coding a digest — the fixture is allowed to grow a line without this
# module having to be edited in lockstep.
ANCHOR = "src/app/main.py:15"
CITING_PAGE = "docs/items/create-item.md"
CITED_FILE = "src/app/main.py"
UNCITED_FILE = "src/cli/main.py"
MANIFEST = "docs/.webster.json"

# src/cli/main.py:1 is `import argparse`. The .md / .mdx pair below cites it, and the committed
# fixture cites it from nowhere else, so a page reaching it is the page under test.
MDX_ANCHOR = "src/cli/main.py:1"
# A third resolvable line, cited by nothing in the committed fixture, for the tests that need a
# citation the pages grew AFTER the record: it resolves like any other anchor, and the record
# holds neither an entry nor a digest for it.
GROWN_ANCHOR = "src/cli/main.py:5"
# Written by the latin-1 test and named in that repository's .gitignore, so git has nothing to
# say about it and the per-anchor hash is the only thing left that can notice an edit.
LATIN1_FILE = "src/app/legacy.py"
LATIN1_ANCHOR = "src/app/legacy.py:1"
LATIN1_BEFORE = b"# caf\xe9 loader, latin-1, older than the tree it lives in\n"
LATIN1_AFTER = b"# caf\xe8 loader, latin-1, older than the tree it lives in\n"

# A citation that leaves WEBSTER_ROOT: one package of a monorepo citing the loader it shares
# with its sibling. Every layer of the script accepts it — ANCHOR matches it, collect_anchors
# collects it, resolves() resolves it, record hashes it — and under_root() then drops the file
# it names out of the lists the suspect oracle used to be read off.
OUTSIDE_ROOT_ANCHOR = "../shared/lib.py:1"
OUTSIDE_ROOT_FILE = "shared/lib.py"
OUTSIDE_ROOT_PAGE = "docs/shared-loader.md"

# A code sample that lives beside the page explaining it. Inside the docs tree, so code_paths()
# drops it, and neither .md nor .mdx, so tree_hash() does not cover it either: with the oracle
# read off the filtered lists there was no field left in the envelope that could report it.
IN_DOCS_ANCHOR = "docs/examples/sample.py:1"
IN_DOCS_FILE = "docs/examples/sample.py"
IN_DOCS_PAGE = "docs/examples/running-the-sample.md"

# A file this fixture has never had, cited by a page in a tree where record has never run: an
# anchors half with a finding in it under a status that says nothing was compared.
MISSING_ANCHOR = "src/app/gone.py:3"
MISSING_ANCHOR_PAGE = "docs/gone.md"


# Where ``write_page`` puts the claim, and so the line every anchor in one of its pages is
# cited from: four frontmatter lines between two ``---`` fences, a blank, the H1, a blank.
# Named rather than written into an assertion, so that a change to the page shape below moves
# the expectation with it instead of turning a citation assertion red for its own reason.
WRITE_PAGE_CLAIM_LINE = 10


def write_page(repo: Path, relpath: str, title: str, audience: str, claim: str) -> None:
    """Write one frontmattered page at ``relpath`` carrying ``claim`` and its anchor.

    The three tests below each need a page the committed fixture does not have, and each needs
    it to be a page doc_files() will walk rather than a bare file with an anchor in it.
    """
    page = repo / relpath
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(
        f'---\nsidebar_position: 60\ntitle: "{title}"\ndoc_type: explanation\n'
        f"audience: {audience}\n---\n\n# {title}\n\n{claim}\n",
        encoding="utf-8",
    )

# Same isolation as conftest's own git calls: a developer's ~/.gitconfig reaches
# these repositories through HOME, and commit.gpgsign or core.autocrlf there
# would fail the commit or rewrite the blob on that machine only.
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}
GIT_TIMEOUT = 30


def git_in(
    repo: Path,
    *args: str,
    check: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run one git command inside ``repo`` with a fixed identity and no user config.

    Tests mutate their repository — edit, rename, commit, rebase away — and the
    mutation is the premise of the assertion that follows, so ``check=True`` by
    default makes a broken premise an error at that line instead of a confusing
    failure three lines later.

    ``extra_env`` overrides that isolation for the one test that needs a
    developer's configuration to reach git, and it exists so the premise of that
    test — that the configuration is genuinely in effect — is checked with the
    same helper as everything else rather than with a second copy of this
    subprocess call.
    """
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    env.update(GIT_ENV)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=check,
        timeout=GIT_TIMEOUT,
        env=env,
    )


def outcome(result: subprocess.CompletedProcess) -> str:
    """Both captured streams and the exit code, for an assertion message."""
    return (
        f"exit: {result.returncode}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )


def parsed(result: subprocess.CompletedProcess) -> dict:
    """The JSON envelope the script printed, or an assertion naming what came instead."""
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"expected one JSON envelope on stdout; {exc}\n{outcome(result)}"
        ) from exc


def record(run_script, repo: Path) -> dict:
    """``drift.py record docs`` against ``repo``, asserting it succeeded."""
    result = run_script("drift.py", "record", "docs", cwd=repo)
    assert result.returncode == 0, (
        f"expected exit 0 from record; got {result.returncode}\n{outcome(result)}"
    )
    return parsed(result)


def manifest_of(repo: Path) -> dict:
    return json.loads((repo / MANIFEST).read_text(encoding="utf-8"))


def write_manifest(repo: Path, data: dict) -> None:
    (repo / MANIFEST).write_text(json.dumps(data, indent=2), encoding="utf-8")


def line_of(repo: Path, relpath: str, lineno: int) -> str:
    return (repo / relpath).read_text(encoding="utf-8").splitlines(True)[lineno - 1]


def hash_of(line: str) -> str:
    """FR-003: sha256 of the cited line, whitespace stripped, first 16 hex characters."""
    return hashlib.sha256(line.strip().encode("utf-8")).hexdigest()[:16]


def hash_of_bytes(raw: bytes) -> str:
    """The same digest taken over raw bytes, for a cited line that is not valid UTF-8.

    ``hash_of`` above goes through ``str``, which is only equal to this for a line the
    interpreter can decode. A file in latin-1 has no such text form, and the digest has to be
    over what is on disk, so the expectation is stated here in bytes.
    """
    return hashlib.sha256(raw).hexdigest()[:16]


def write_citing_page(repo: Path, ext: str) -> str:
    """Write ``docs/notes<ext>`` citing ``src/cli/main.py:1`` and return its relative path.

    The two pages this builds differ in one character — the extension — so the pair of tests
    that use it can show that a page is a page under either name. Shared rather than
    parametrized on purpose: ``test_readme.py`` counts one collected item per top-level test
    function and refuses a parametrized module by name, so the twins are written out.
    """
    page = f"docs/notes{ext}"
    (repo / page).write_text(
        '---\nsidebar_position: 50\ntitle: "Notes"\ndoc_type: explanation\n'
        "audience: user\n---\n\n# Notes\n\n"
        f"The command line parses your arguments first. <!-- {MDX_ANCHOR} -->\n",
        encoding="utf-8",
    )
    return page


def record_then_touch_uncited(run_script, repo: Path, ext: str) -> tuple:
    """Add the page, record, append a line to the file it cites, and check.

    The cited file is ``src/cli/main.py``, which no page in the committed fixture cites, so the
    only thing that can make it suspect is the page this helper just wrote.
    """
    page = write_citing_page(repo, ext)
    record(run_script, repo)
    target = repo / UNCITED_FILE
    target.write_text(target.read_text(encoding="utf-8") + "# touched\n", encoding="utf-8")
    result = run_script("drift.py", "check", "docs", cwd=repo)
    return page, result, parsed(result)


# ---------------------------------------------------------------------------
# FR-001: git status --porcelain -z, both halves of a rename
# ---------------------------------------------------------------------------
def test_uncommitted_edit_to_cited_file_is_drift(run_script, fixture_repo):
    """FR-001 / AC-001 / OT-001 — the false clean this casting exists to stop.

    RED at cfafe8e: ``git()`` stripped the output of ``git status --short``,
    which removed the leading space from the first ` M src/app/main.py` record;
    the ``[3:]`` slice that followed cut the path down to ``rc/app/main.py``, no
    anchor matched it, and the check printed status clean at exit 0.
    """
    record(run_script, fixture_repo)
    cited = fixture_repo / CITED_FILE
    cited.write_text(cited.read_text(encoding="utf-8") + "# edit\n", encoding="utf-8")

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "drift", (
        f"expected status drift for an uncommitted edit to {CITED_FILE}; "
        f"got {data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1 alongside status drift; got {result.returncode}\n{outcome(result)}"
    )
    assert data["suspect_pages"] == {CITING_PAGE: [ANCHOR]}, (
        f"expected {CITING_PAGE} suspect via {ANCHOR}; got {data['suspect_pages']!r}\n"
        f"{outcome(result)}"
    )
    assert data["code_files_changed"] == 1, (
        f"expected 1 code file changed; got {data['code_files_changed']}\n{outcome(result)}"
    )


def test_staged_rename_marks_old_and_new_path_dirty(run_script, fixture_repo):
    """FR-001 / AC-002 / OT-002 — a rename record carries two paths, and both count.

    RED at cfafe8e: ``R  src/app/main.py -> src/app/server.py`` was sliced to
    ``rc/app/main.py -> src/app/server.py``, so the page kept citing a file that
    had moved and suspect_pages stayed empty.
    """
    record(run_script, fixture_repo)
    git_in(fixture_repo, "mv", CITED_FILE, "src/app/server.py")

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert result.returncode == 1, (
        f"expected exit 1 for a renamed cited file; got {result.returncode}\n{outcome(result)}"
    )
    assert data["status"] == "drift", (
        f"expected status drift; got {data['status']!r}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 2, (
        "expected both the old and the new path of the rename to count as dirty; got "
        f"{data['code_files_changed']}\n{outcome(result)}"
    )
    assert data["suspect_pages"].get(CITING_PAGE) == [ANCHOR], (
        f"expected {CITING_PAGE} suspect through the OLD path {ANCHOR}; got "
        f"{data['suspect_pages']!r}\n{outcome(result)}"
    )
    assert [b["anchor"] for b in data["broken_anchors"]] == [ANCHOR], (
        f"expected {ANCHOR} reported broken once the file moved; got "
        f"{data['broken_anchors']!r}\n{outcome(result)}"
    )


def test_docs_below_the_git_root_still_match_anchors(run_script, fixture_repo, tmp_path):
    """FR-001 / US-001 / AC-001 / ST-001 — git names paths from the repository root.

    Every path in ``git status --porcelain`` is relative to the repository root, never to the
    directory the command ran in. ``git diff --name-only`` is too, but only because the script
    pins ``--no-relative``: ``diff.relative`` in a developer's config makes that diff name its
    paths from the directory the command ran in, which
    ``test_diff_relative_config_still_reports_drift`` below pins. Anchors, pages and the docs
    prefix are all relative to WEBSTER_ROOT. Those are one namespace only when WEBSTER_ROOT is
    itself the repository root, which is the layout the rest of this module builds and is not
    the layout of an ordinary monorepo.

    RED at f31b144: with the project one directory down, git said ``site/src/app/main.py``
    where the anchor said ``src/app/main.py``, so the two never intersected. suspect_pages came
    back empty on every run and ``site/docs/.webster.json`` failed the inside-docs test and was
    counted as a changed code file — so AC-001's own scenario, one directory lower, printed
    unrelated_changes at exit 0 with a note reading "no page cites any of them" while the page
    sat in the tree citing it. No configuration reaches this state; being in a subdirectory is
    the whole trigger.
    """
    mono = tmp_path / "mono"
    site = mono / "site"
    mono.mkdir()
    shutil.copytree(fixture_repo, site, ignore=shutil.ignore_patterns(".git"))
    git_in(mono, "-c", "init.defaultBranch=main", "init", "-q", ".")
    git_in(mono, "add", "-A")
    git_in(mono, "commit", "-q", "-m", "the project one directory below the repository root")

    prefix = git_in(site, "rev-parse", "--show-prefix").stdout.strip()
    assert prefix == "site/", (
        "this test needs the docs one directory below the git root; git reports the prefix "
        f"as {prefix!r}"
    )

    record(run_script, site)
    cited = site / CITED_FILE
    cited.write_text(cited.read_text(encoding="utf-8") + "# edit\n", encoding="utf-8")

    result = run_script("drift.py", "check", "docs", cwd=site)
    data = parsed(result)

    assert data["suspect_pages"] == {CITING_PAGE: [ANCHOR]}, (
        f"expected {CITING_PAGE} suspect through {ANCHOR} with the repository root one "
        f"directory up; got {data['suspect_pages']!r}\n{outcome(result)}"
    )
    assert data["status"] == "drift", (
        f"expected status drift for an uncommitted edit to {CITED_FILE}; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1 alongside status drift; got {result.returncode}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 1, (
        "expected only the edited source file to count: the manifest, which git names "
        f"site/{MANIFEST}, is inside the docs tree and is not code; got "
        f"{data['code_files_changed']}\n{outcome(result)}"
    )


def test_diff_relative_config_still_reports_drift(run_script, fixture_repo, tmp_path):
    """FR-001 / FR-005 / US-001 / AC-001 / ST-001 — one line of ~/.gitconfig, US-001 negated.

    ``diff.relative = true`` makes ``git diff --name-only`` name its paths from the directory
    the command ran in instead of from the repository root. ``git status --porcelain`` has no
    such setting, so the two halves of the code check stop agreeing on what a path is the
    moment the docs sit below the repository root — and the diff half is the only half that
    can see a change that has already been COMMITTED.

    RED at 944c958: status clean at exit 0. The diff said ``src/app/main.py`` where
    ``repo_prefix()`` said ``site/``, ``under_root()`` dropped it as living outside ROOT,
    ``changed`` came back empty, and a committed edit to the file ``docs/items/create-item.md``
    cites was reported as a tree where nothing at all had happened. The edit below is appended
    past the cited line on purpose: line 15 is unchanged, so its recorded digest still matches
    and the hash half cannot cover for the diff half.

    The suite could not see this because no machine it has run on sets ``diff.relative``, and
    not because the setting is fenced off from the script — which is what an earlier version of
    this paragraph claimed. ``conftest.base_env`` hands ``drift.py`` PATH and HOME and nothing
    else, so a developer's ``~/.gitconfig`` reaches every git call the script makes, in every
    test in this module. What pins ``GIT_CONFIG_GLOBAL`` at ``os.devnull`` is ``git_in`` above
    and conftest's fixture builder: this module's OWN git commands, correctly isolated so that a
    developer's settings cannot change a premise. Measured at 457658b, before ``--no-relative``
    was pinned: the committed edit below prints clean under a HOME carrying ``diff.relative``
    and drift under one without, both times through the ordinary PATH-and-HOME environment.
    This is the one test that pins the configuration the SCRIPT sees, passing
    ``GIT_CONFIG_GLOBAL`` through ``run_script`` at a file it writes itself — the same isolation
    with one setting in it.
    """
    relative_config = tmp_path / "gitconfig-diff-relative"
    relative_config.write_text("[diff]\n\trelative = true\n", encoding="utf-8")
    developer = {"GIT_CONFIG_GLOBAL": str(relative_config), "GIT_CONFIG_NOSYSTEM": "1"}

    mono = tmp_path / "mono"
    site = mono / "site"
    mono.mkdir()
    shutil.copytree(fixture_repo, site, ignore=shutil.ignore_patterns(".git"))
    git_in(mono, "-c", "init.defaultBranch=main", "init", "-q", ".")
    git_in(mono, "add", "-A")
    git_in(mono, "commit", "-q", "-m", "the project one directory below the repository root")

    prefix = git_in(site, "rev-parse", "--show-prefix").stdout.strip()
    assert prefix == "site/", (
        "this test needs the docs one directory below the git root; git reports the prefix "
        f"as {prefix!r}"
    )

    recorded = record(run_script, site)["gitHead"]
    cited = site / CITED_FILE
    cited.write_text(cited.read_text(encoding="utf-8") + "\n# committed edit\n", encoding="utf-8")
    git_in(site, "add", "--", CITED_FILE)
    git_in(site, "commit", "-q", "-m", "edit the cited file and commit it")

    # The premise: with that configuration in force the diff really does answer in the wrong
    # namespace. Without this the test would still pass if the config silently failed to load,
    # and would then be pinning nothing.
    named = git_in(
        site, "diff", "--name-only", f"{recorded}..HEAD", extra_env=developer
    ).stdout.split()
    assert named == [CITED_FILE], (
        f"this test needs diff.relative in force, which names the change {CITED_FILE!r} from "
        f"the cwd rather than site/{CITED_FILE}; git named {named!r}"
    )

    result = run_script("drift.py", "check", "docs", cwd=site, env=developer)
    data = parsed(result)

    assert data["hash_mismatches"] == [], (
        "this test needs the committed edit to leave the cited line alone, so that only the "
        f"diff half can notice it; got {data['hash_mismatches']!r}\n{outcome(result)}"
    )
    assert data["suspect_pages"] == {CITING_PAGE: [ANCHOR]}, (
        f"expected {CITING_PAGE} suspect through {ANCHOR} with diff.relative set; got "
        f"{data['suspect_pages']!r}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 1, (
        f"expected the committed edit to {CITED_FILE} to count as one changed code file; got "
        f"{data['code_files_changed']}\n{outcome(result)}"
    )
    assert data["status"] == "drift", (
        f"expected status drift for a committed edit to {CITED_FILE}; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1 alongside status drift; got {result.returncode}\n{outcome(result)}"
    )


def test_cited_file_outside_the_docs_root_is_drift(run_script, fixture_repo, tmp_path):
    """FR-005 / US-001 / ST-001 / ST-006 — a ``../`` citation is a citation.

    One package of a monorepo may cite a file it shares with its sibling, and every layer of
    this script accepts that citation: ANCHOR matches it, ``collect_anchors`` collects it,
    ``resolves()`` resolves it, and ``record`` writes it a ``lineHashes`` entry. The manifest
    assertion below pins that, so this test cannot come out green by the citation quietly
    ceasing to be one.

    RED at 693570d: status clean at exit 0, with every field that could have reported the
    edit — suspect_pages, code_files_changed, hash_mismatches, broken_anchors — empty.
    ``under_root()`` dropped ``shared/lib.py`` for not starting with the ``site/`` prefix —
    right for the code-churn count, which is a number about this docs set's own scope — and the
    suspect oracle was read off the list that drop produced, so the one file this page cites
    was the one file git's answer could not be about. ``under_root()``'s docstring said no
    anchor could name such a path, "every anchor resolves through os.path.join(ROOT, target)";
    ``os.path.join`` does not confine what it returns.

    The edit lands past the cited line on purpose. Line 1 is untouched, so its recorded digest
    still matches and the line half cannot cover for the file signal that went missing.
    """
    mono = tmp_path / "mono"
    site = mono / "site"
    mono.mkdir()
    shutil.copytree(fixture_repo, site, ignore=shutil.ignore_patterns(".git"))
    (mono / "shared").mkdir()
    (mono / OUTSIDE_ROOT_FILE).write_text(
        "def load():\n    return 1\n# a line no page cites\n", encoding="utf-8"
    )
    write_page(
        site,
        OUTSIDE_ROOT_PAGE,
        "Shared loader",
        "developer",
        f"The loader comes from the sibling package. <!-- {OUTSIDE_ROOT_ANCHOR} -->",
    )
    git_in(mono, "-c", "init.defaultBranch=main", "init", "-q", ".")
    git_in(mono, "add", "-A")
    git_in(mono, "commit", "-q", "-m", "a site beside the package it shares a loader with")

    prefix = git_in(site, "rev-parse", "--show-prefix").stdout.strip()
    assert prefix == "site/", (
        "this test needs the docs one directory below the git root, with the cited file in "
        f"the other; git reports the prefix as {prefix!r}"
    )

    recorded = record(run_script, site)["gitHead"]
    assert OUTSIDE_ROOT_ANCHOR in manifest_of(site)["lineHashes"], (
        f"this test needs {OUTSIDE_ROOT_ANCHOR} recorded as an ordinary anchor; the manifest "
        f"holds {sorted(manifest_of(site)['lineHashes'])!r}"
    )

    lib = mono / OUTSIDE_ROOT_FILE
    lib.write_text(
        lib.read_text(encoding="utf-8") + "# committed edit, not the cited line\n",
        encoding="utf-8",
    )
    git_in(mono, "add", "--", OUTSIDE_ROOT_FILE)
    git_in(mono, "commit", "-q", "-m", "edit the shared loader away from the cited line")

    # The premise: git names the change, and names it from the repository root.
    named = git_in(
        site, "diff", "--name-only", "--no-relative", f"{recorded}..HEAD"
    ).stdout.split()
    assert OUTSIDE_ROOT_FILE in named, (
        f"this test needs git to name {OUTSIDE_ROOT_FILE} as changed since the record; git "
        f"named {named!r}"
    )

    result = run_script("drift.py", "check", "docs", cwd=site)
    data = parsed(result)

    assert data["hash_mismatches"] == [], (
        "this test needs the committed edit to leave the cited line alone, so that only the "
        f"file signal can notice it; got {data['hash_mismatches']!r}\n{outcome(result)}"
    )
    assert data["suspect_pages"] == {OUTSIDE_ROOT_PAGE: [OUTSIDE_ROOT_ANCHOR]}, (
        f"expected {OUTSIDE_ROOT_PAGE} suspect through {OUTSIDE_ROOT_ANCHOR}; got "
        f"{data['suspect_pages']!r}\n{outcome(result)}"
    )
    assert data["status"] == "drift", (
        f"expected status drift for a committed edit to the cited {OUTSIDE_ROOT_FILE}; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1 alongside status drift; got {result.returncode}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 0, (
        "code_files_changed counts this docs set's own scope and the shared package is "
        "outside it, so the narrowing under_root() does for the count stays in force; only "
        f"the oracle moved. got {data['code_files_changed']}\n{outcome(result)}"
    )


def test_cited_file_inside_the_docs_tree_is_drift(run_script, fixture_repo):
    """FR-005 / US-001 / ST-001 / ST-006 — a code sample under docs/ is still cited code.

    RED at 693570d: status clean at exit 0, and no field of the envelope reported the edit:
    suspect_pages, code_files_changed, hash_mismatches and docs_edited_since_record were all
    empty or false at once. ``code_paths()`` filters the docs tree out of the changed and dirty lists so that an author
    replacing a screenshot is not told code changed — right for the count — and the suspect
    oracle was read off those filtered lists, so the sample lost its only git signal.
    ``tree_hash()`` covers .md and .mdx alone and did not cover it either, so
    ``docs_edited_since_record`` stayed false as well.

    The count is asserted at 0 beside the status: the filtering this fix keeps and the oracle
    it moves are two different populations, and widening the count instead would report an
    author's ordinary docs edit as code churn.
    """
    write_page(
        fixture_repo,
        IN_DOCS_PAGE,
        "Running the sample",
        "user",
        f"The sample runs on its own. <!-- {IN_DOCS_ANCHOR} -->",
    )
    sample = fixture_repo / IN_DOCS_FILE
    sample.write_text('import sys\n\nprint("hello", file=sys.stdout)\n', encoding="utf-8")
    git_in(fixture_repo, "add", "-A")
    git_in(fixture_repo, "commit", "-q", "-m", "a runnable sample and the page explaining it")

    recorded = record(run_script, fixture_repo)["gitHead"]
    assert IN_DOCS_ANCHOR in manifest_of(fixture_repo)["lineHashes"], (
        f"this test needs {IN_DOCS_ANCHOR} recorded as an ordinary anchor; the manifest holds "
        f"{sorted(manifest_of(fixture_repo)['lineHashes'])!r}"
    )

    sample.write_text(
        sample.read_text(encoding="utf-8") + 'print("and again")\n', encoding="utf-8"
    )
    git_in(fixture_repo, "add", "--", IN_DOCS_FILE)
    git_in(fixture_repo, "commit", "-q", "-m", "extend the sample past the cited line")

    named = git_in(
        fixture_repo, "diff", "--name-only", "--no-relative", f"{recorded}..HEAD"
    ).stdout.split()
    assert IN_DOCS_FILE in named, (
        f"this test needs git to name {IN_DOCS_FILE} as changed since the record; git named "
        f"{named!r}"
    )

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["hash_mismatches"] == [], (
        "this test needs the committed edit to leave the cited line alone, so that only the "
        f"file signal can notice it; got {data['hash_mismatches']!r}\n{outcome(result)}"
    )
    assert data["docs_edited_since_record"] is False, (
        "this test needs the docs-tree digest to stay put, so that the oracle is the only "
        f"thing that can report the sample; got {data['docs_edited_since_record']!r}\n"
        f"{outcome(result)}"
    )
    assert data["code_files_changed"] == 0, (
        "a file inside the docs tree is not code churn and the count still says so; got "
        f"{data['code_files_changed']}\n{outcome(result)}"
    )
    assert data["suspect_pages"] == {IN_DOCS_PAGE: [IN_DOCS_ANCHOR]}, (
        f"expected {IN_DOCS_PAGE} suspect through {IN_DOCS_ANCHOR}; got "
        f"{data['suspect_pages']!r}\n{outcome(result)}"
    )
    assert data["status"] == "drift", (
        f"expected status drift for a committed edit to the cited {IN_DOCS_FILE}; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1 alongside status drift; got {result.returncode}\n{outcome(result)}"
    )


def test_docs_tree_at_the_repo_root_reaches_clean(run_script, fixture_repo):
    """FR-001 / FR-005 / US-001 / US-002 / ST-006 — WEBSTER_DOCS="." is a real layout.

    RED at 19941ad: the docs prefix was ``os.path.relpath(DOCS, ROOT)`` with a "/" appended,
    which is "./" when the docs directory IS the root — a repository whose pages sit at the
    top, which is what WEBSTER_DOCS="." asks for. Nothing git prints is "./"-prefixed, so the
    prefix test matched no path at all and ``.webster.json``, the file record had just written,
    counted as a changed code file. ``clean`` was unreachable on any tree in this layout: a
    fresh record followed immediately by a check printed unrelated_changes at exit 0 with
    code_files_changed 1 and a note about code that no page cites, naming the manifest.

    The second half is the guard against fixing that by declaring everything under the root
    docs. Source files have to stay code, or no page could ever go suspect for one — which
    would move US-001's own scenario into the same false clean under a different name.
    """
    at_root = {"WEBSTER_DOCS": "."}
    printed = run_script("drift.py", "record", cwd=fixture_repo, env=at_root)
    assert printed.returncode == 0, (
        f"expected exit 0 from record at the repo root; got {printed.returncode}\n"
        f"{outcome(printed)}"
    )
    assert (fixture_repo / ".webster.json").is_file(), (
        f"this test needs the manifest written at the repository root\n{outcome(printed)}"
    )

    result = run_script("drift.py", "check", cwd=fixture_repo, env=at_root)
    data = parsed(result)

    assert data["status"] == "clean", (
        "expected status clean straight after a record with the docs tree at the repo root; "
        f"got {data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"expected exit 0 alongside status clean; got {result.returncode}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 0, (
        "expected the manifest this run just wrote not to count as changed code; got "
        f"{data['code_files_changed']}\n{outcome(result)}"
    )

    # A source file is still code in this layout, so the page citing it still goes suspect.
    cited = fixture_repo / CITED_FILE
    cited.write_text(cited.read_text(encoding="utf-8") + "# edit\n", encoding="utf-8")

    result = run_script("drift.py", "check", cwd=fixture_repo, env=at_root)
    data = parsed(result)

    assert data["suspect_pages"] == {CITING_PAGE: [ANCHOR]}, (
        f"expected {CITING_PAGE} suspect through {ANCHOR} with the docs tree at the repo "
        f"root; got {data['suspect_pages']!r}\n{outcome(result)}"
    )
    assert data["status"] == "drift" and result.returncode == 1, (
        f"expected drift at exit 1 for an uncommitted edit to {CITED_FILE}; got "
        f"{data['status']!r} at exit {result.returncode}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 1, (
        f"expected only {CITED_FILE} to count as code; got "
        f"{data['code_files_changed']}\n{outcome(result)}"
    )


# ---------------------------------------------------------------------------
# FR-002 / FR-006 / FR-037: git failures are statuses, not empty strings
# ---------------------------------------------------------------------------
def test_check_outside_a_git_repo_reports_no_git(run_script, fixture_repo):
    """FR-002 / FR-006 / AC-003 / OT-003 — no repository is exit 2, anchors still reported.

    RED at cfafe8e: ``git()`` swallowed the failure into "", so the check
    compared nothing, found the broken anchor only, and exited 1 calling it
    drift — the right code for the wrong reason, and clean whenever no anchor
    happened to be broken.
    """
    record(run_script, fixture_repo)
    # Truncating the cited file breaks the anchor, which is what makes "the
    # anchors half still runs" (ST-004) an observable claim rather than a key
    # that happens to hold an empty list.
    (fixture_repo / CITED_FILE).write_text("only one line\n", encoding="utf-8")
    shutil.rmtree(fixture_repo / ".git")

    probe = git_in(fixture_repo, "rev-parse", "--show-toplevel", check=False)
    assert probe.returncode != 0, (
        "this test needs a directory that is not inside any git repository, and git "
        f"reported one at {probe.stdout.strip()!r}. Check TMPDIR.\n{outcome(probe)}"
    )

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "no_git", (
        f"expected status no_git without a repository; got {data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 2, (
        f"expected exit 2 for a check that could not run; got {result.returncode}\n"
        f"{outcome(result)}"
    )
    assert [b["anchor"] for b in data["broken_anchors"]] == [ANCHOR], (
        f"expected the anchors half to still report {ANCHOR} broken; got "
        f"{data['broken_anchors']!r}\n{outcome(result)}"
    )
    assert data["note"], f"expected a note explaining no_git\n{outcome(result)}"


def test_rebased_away_head_reports_head_missing(run_script, fixture_repo):
    """FR-006 / FR-037 / AC-004 / OT-004 — a recorded commit the repo no longer has.

    RED at cfafe8e: ``git diff --name-only OLD..HEAD`` exits 128 when OLD is
    gone, ``git()`` turned that into "", changed came back empty and the check
    printed clean at exit 0.

    ``reset --hard`` alone does not reproduce it, and that is a conflict inside
    the spec rather than a quirk of this test. AC-004 and OT-004 name ``git
    reset --hard HEAD~1`` as the trigger; Locked FR-006 defines head_missing by
    ``git rev-parse --verify --quiet SHA^{commit}`` failing. After a reset the
    commit is still in the object store with the reflog pointing at it, so
    rev-parse finds it and the status stays clean until gc runs — under the
    AC-004 wording alone the answer would depend on when the repository last
    collected garbage. FR-006's definition governs, so the trigger is all three
    of ``reset --hard``, ``reflog expire --expire=now --all`` and ``gc
    --prune=now``, and the ``cat-file -e`` probe below refuses to run the check
    until the commit is demonstrably gone. drift.py's ``commit_exists`` carries
    the same note.
    """
    recorded = record(run_script, fixture_repo)["gitHead"]
    git_in(fixture_repo, "reset", "--hard", "-q", "HEAD~1")
    git_in(fixture_repo, "reflog", "expire", "--expire=now", "--all")
    git_in(fixture_repo, "gc", "--prune=now", "-q")

    gone = git_in(fixture_repo, "cat-file", "-e", f"{recorded}^{{commit}}", check=False)
    assert gone.returncode != 0, (
        f"this test needs the recorded commit {recorded} to be genuinely absent; git still "
        f"has it after the reflog expire and gc\n{outcome(gone)}"
    )

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "head_missing", (
        f"expected status head_missing for a rebased-away {recorded[:12]}; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 2, (
        f"expected exit 2 for head_missing; got {result.returncode}\n{outcome(result)}"
    )
    assert data["gitHead"]["recorded"] == recorded, (
        f"expected the missing sha reported back; got {data['gitHead']!r}\n{outcome(result)}"
    )


def test_non_ascii_path_under_a_c_locale_still_answers(run_script, fixture_repo):
    """FR-002 / FR-037 / CT-001 — a byte git prints is not a reason to traceback.

    RED at 9859317: stderr carried a UnicodeDecodeError traceback and the
    script exited 1 having printed no JSON at all. ``git()`` ran with
    ``text=True``, which decodes with the locale's preferred encoding — US-ASCII
    under LC_ALL=C once PEP 538 locale coercion and PEP 540 UTF-8 mode are both
    switched off. The first non-ASCII byte of a path in
    ``git status --porcelain -z`` raised UnicodeDecodeError, which is a
    ValueError and therefore not one of the ``(OSError,
    subprocess.SubprocessError)`` the except clause names, so it escaped main().

    Exit 1 is the drift code. A caller handed exit 1 by a crash is being told
    the pages disagree with the code when nothing was measured at all, which is
    the same false answer under the opposite sign as the clean this file exists
    to stop. FR-002 wants every git failure to arrive as GitUnavailable, and a
    decode failure is a git failure.
    """
    record(run_script, fixture_repo)
    # Untracked, uncited, and named in bytes US-ASCII cannot represent:
    # --untracked-files=all puts it in the porcelain record, where the decode
    # happens, and no anchor points at it so the answer is about the locale
    # rather than about this file.
    (fixture_repo / "src" / "app" / "caf\u00e9.py").write_text(
        "# uncited helper\n", encoding="utf-8"
    )

    # PYTHONCOERCECLOCALE=0 blocks the PEP 538 coercion of C to C.UTF-8 and
    # PYTHONUTF8=0 blocks PEP 540 UTF-8 mode; without both, CPython quietly
    # hands the subprocess a UTF-8 locale and the decode never fails.
    result = run_script(
        "drift.py", "check", "docs",
        cwd=fixture_repo,
        env={"LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
    )

    assert "Traceback" not in result.stderr, (
        f"expected git() to absorb the decode, not to raise through main()\n{outcome(result)}"
    )
    data = parsed(result)
    assert data["status"] == "unrelated_changes", (
        f"expected an uncited untracked file to read as unrelated_changes; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"expected exit 0 from the CT-001 contract; got {result.returncode}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 1, (
        f"expected the non-ASCII path to be counted, not dropped; got "
        f"{data['code_files_changed']}\n{outcome(result)}"
    )


def test_unparseable_manifest_reports_no_manifest(run_script, fixture_repo):
    """FR-006 / CT-001 — a manifest that cannot be read is exit 2, not a traceback at exit 1.

    RED at 19c97f9: the manifest was read with a bare
    ``old = json.load(open(MANIFEST))`` — no try around it and no ``encoding=`` — so a torn
    write or a merge conflict left in the file raised JSONDecodeError straight out of main().
    The script printed nothing at all on stdout and exited 1, the code FR-006 reserves for
    drift, with a traceback on stderr. Nothing had been measured and the caller was told the
    pages disagree with the code — the same class as the swallowed git error ``GitUnavailable``
    was added to stop.
    """
    record(run_script, fixture_repo)
    # What `git merge` leaves behind when two branches both re-recorded.
    (fixture_repo / MANIFEST).write_text(
        "<<<<<<< HEAD\n"
        '{\n  "gitHead": "0000000000000000000000000000000000000000",\n'
        "=======\n"
        '{\n  "gitHead": "1111111111111111111111111111111111111111",\n'
        ">>>>>>> other\n",
        encoding="utf-8",
    )

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)

    assert "Traceback" not in result.stderr, (
        f"expected the unreadable manifest to be reported, not raised\n{outcome(result)}"
    )
    data = parsed(result)
    assert data["status"] == "no_manifest", (
        f"expected status no_manifest for a manifest that will not parse; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 2, (
        f"expected exit 2 for a check that could not run; got {result.returncode}\n"
        f"{outcome(result)}"
    )
    assert "JSONDecodeError" in data["note"], (
        f"expected the note to name the parse error so the reader knows what to fix; got "
        f"{data['note']!r}\n{outcome(result)}"
    )

    # Valid JSON that is not an object is the same failure one step later: every read of the
    # manifest in check() is `old.get(...)`, so one holding a list raised AttributeError out
    # of main() and exited 1 for the same wrong reason.
    (fixture_repo / MANIFEST).write_text("[]\n", encoding="utf-8")

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)

    assert "Traceback" not in result.stderr, (
        f"expected a JSON array to be reported, not raised\n{outcome(result)}"
    )
    data = parsed(result)
    assert data["status"] == "no_manifest" and result.returncode == 2, (
        f"expected no_manifest at exit 2 for a manifest that is not an object; got "
        f"{data['status']!r} at exit {result.returncode}\n{outcome(result)}"
    )
    assert data["note"], (
        f"expected a note saying what was found instead\n{outcome(result)}"
    )


def test_wrong_typed_manifest_field_is_no_manifest(run_script, fixture_repo):
    """FR-006 / CT-001 — a field of the wrong type is reported, not raised.

    RED at f31b144: ``"gitHead": 17`` is truthy, so the check asked git whether ``17^{commit}``
    existed, git said no, and the head_missing note sliced it — ``recorded[:12]`` on an int
    raised TypeError out of main(). The script printed nothing on stdout and exited 1, the code
    FR-006 reserves for drift, with a traceback on stderr: a run that measured nothing telling
    its caller the pages disagree with the code. A lineHashes that is not an object took the
    quiet half of the same failure, reported ``hashes: not_recorded`` and passed at exit 0.

    ``read_manifest`` already promised "(None, reason) for every unusable state", and a
    manifest is a file people edit, merge and generate, so a field holding the wrong type is an
    ordinary state of it — the same class as the unparseable manifest above, one field deeper.

    The list below is every field ``check`` reads back, and only those. ``docsHash`` was the
    one it read and nothing validated: a non-string never equals a fresh tree digest, so
    ``docs_edited_since_record`` came back true on a tree nobody had touched and the run
    printed unrelated_changes at exit 0 — the quiet half of this failure, a wrong answer in
    the shape of a right one rather than a traceback. ``pages`` is written by record and read
    by nothing, so its type is not checked here; refusing a manifest over a field that changes
    no answer is the refusal this test was reported for.
    """
    record(run_script, fixture_repo)
    good = manifest_of(fixture_repo)

    # int, bool, float and object all reach the same slice; the field is named in the note so
    # the reader is told which one to fix rather than only that something is wrong.
    for field, value in (
        ("gitHead", 17),
        ("gitHead", True),
        ("gitHead", 1.5),
        ("gitHead", {"sha": "0" * 40}),
        ("docsHash", 5),
        ("docsHash", ["0" * 64]),
        ("lineHashes", [ANCHOR]),
        ("lineHashes", {ANCHOR: 5}),
        ("anchors", ANCHOR),
    ):
        wrong = dict(good)
        wrong[field] = value
        write_manifest(fixture_repo, wrong)

        result = run_script("drift.py", "check", "docs", cwd=fixture_repo)

        assert "Traceback" not in result.stderr, (
            f"expected {field}={value!r} to be reported, not raised\n{outcome(result)}"
        )
        data = parsed(result)
        assert data["status"] == "no_manifest", (
            f"expected no_manifest for {field}={value!r}; got {data['status']!r}\n"
            f"{outcome(result)}"
        )
        assert result.returncode == 2, (
            f"expected exit 2 for {field}={value!r}, never the exit 1 that means drift; got "
            f"{result.returncode}\n{outcome(result)}"
        )
        assert field in data["note"], (
            f"expected the note to name {field} as the field that is wrong; got "
            f"{data['note']!r}\n{outcome(result)}"
        )


def test_no_manifest_envelope_lists_broken_anchors(run_script, fixture_repo):
    """AC-003 / CT-001 / FR-006 — one key for the broken-anchor list, under every status with one.

    RED at 693570d: the no_manifest envelope published the list under ``broken`` while every
    other status that publishes it uses ``broken_anchors`` — the name the module docstring and
    ``commands/audit.md`` both give as THE field to read. A consumer reading broken_anchors got
    nothing back from a no_manifest run, which is the run most likely to be holding broken
    anchors, nothing here having been recorded yet.

    Nothing in this suite read either key under this status, so emptying the list entirely was
    invisible. The assertion is on the anchor rather than on the key alone, for that reason.
    """
    write_page(
        fixture_repo,
        MISSING_ANCHOR_PAGE,
        "A claim about code that is gone",
        "developer",
        f"The handler was removed in the rewrite. <!-- {MISSING_ANCHOR} -->",
    )

    # No record has run in this tree: the fixture ships without a manifest (GI-005), which is
    # what puts the run under no_manifest with an anchors half that has already found a finding.
    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "no_manifest" and result.returncode == 2, (
        f"expected no_manifest at exit 2 with no manifest recorded; got {data['status']!r} at "
        f"exit {result.returncode}\n{outcome(result)}"
    )
    assert "broken" not in data, (
        "the broken-anchor list is published under one key across every status that carries "
        "one; got a second "
        f"key `broken` beside broken_anchors\n{outcome(result)}"
    )
    assert [b["anchor"] for b in data["broken_anchors"]] == [MISSING_ANCHOR], (
        f"expected {MISSING_ANCHOR} listed under broken_anchors on a no_manifest run; got "
        f"{data['broken_anchors']!r}\n{outcome(result)}"
    )
    cited_from = f"{MISSING_ANCHOR_PAGE}:{WRITE_PAGE_CLAIM_LINE}"
    assert data["broken_anchors"][0]["cited_by"] == [cited_from], (
        f"expected the broken anchor to name {cited_from} as the page to fix; got "
        f"{data['broken_anchors'][0]['cited_by']!r}\n{outcome(result)}"
    )


# ---------------------------------------------------------------------------
# FR-003 / FR-004 / FR-034: the per-anchor line hash
# ---------------------------------------------------------------------------
def test_record_writes_a_line_hash_per_anchor(run_script, fixture_repo):
    """FR-003 / OT-005 / CT-002 — lineHashes is a top-level 16-hex map at record.

    RED at cfafe8e: the manifest had no lineHashes key, so a cited line could
    be rewritten in place and nothing in the manifest disagreed.
    """
    printed = record(run_script, fixture_repo)
    manifest = manifest_of(fixture_repo)

    assert manifest["lineHashes"] == {ANCHOR: hash_of(line_of(fixture_repo, CITED_FILE, 15))}, (
        f"expected one lineHashes entry for {ANCHOR} equal to the stripped-line digest; got "
        f"{manifest.get('lineHashes')!r}"
    )
    assert re.fullmatch(r"[0-9a-f]{16}", manifest["lineHashes"][ANCHOR]), (
        f"expected a 16-character hex digest; got {manifest['lineHashes'][ANCHOR]!r}"
    )
    assert manifest["anchors"] == {ANCHOR: [f"{CITING_PAGE}:12"]}, (
        f"expected the anchors map to keep its list shape; got {manifest['anchors']!r}"
    )
    assert printed["lineHashes"] == 1, (
        f"expected record to report 1 hashed anchor; got {printed!r}"
    )


def test_changed_cited_line_is_reported_as_a_mismatch(run_script, fixture_repo):
    """FR-003 / AC-005 / OT-006 — same line number, different content, committed.

    RED at cfafe8e: there was no hash_mismatches key to hold the answer, so
    the only signal was that some file in the diff happened to be cited.
    """
    record(run_script, fixture_repo)
    lines = (fixture_repo / CITED_FILE).read_text(encoding="utf-8").splitlines(True)
    lines[14] = "# the cited line now says something else\n"
    (fixture_repo / CITED_FILE).write_text("".join(lines), encoding="utf-8")
    git_in(fixture_repo, "add", "--", CITED_FILE)
    git_in(fixture_repo, "commit", "-q", "-m", "edit the cited line in place")

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["hash_mismatches"] == [ANCHOR], (
        f"expected {ANCHOR} under hash_mismatches; got {data['hash_mismatches']!r}\n"
        f"{outcome(result)}"
    )
    assert data["status"] == "drift", (
        f"expected status drift; got {data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n{outcome(result)}"
    )


def test_hash_mismatch_alone_is_drift(run_script, fixture_repo):
    """FR-007 — a differing hash forces drift with nothing else to go on.

    The recorded digest is overwritten rather than the file, on purpose: a real
    edit also shows up in the diff and in the dirty set, so it cannot show that
    the hash half decides anything by itself. Here the work tree is untouched and
    git has nothing to report, and the check must still refuse to say clean.

    RED at cfafe8e: status clean, exit 0.
    """
    record(run_script, fixture_repo)
    manifest = manifest_of(fixture_repo)
    manifest["lineHashes"][ANCHOR] = "0" * 16
    write_manifest(fixture_repo, manifest)

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "drift", (
        f"expected a recorded hash that no longer matches to force drift; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 0, (
        f"expected git to have nothing to report in this scenario; got "
        f"{data['code_files_changed']}\n{outcome(result)}"
    )
    assert data["hash_mismatches"] == [ANCHOR], (
        f"expected {ANCHOR} under hash_mismatches; got {data['hash_mismatches']!r}\n"
        f"{outcome(result)}"
    )


def test_manifest_without_line_hashes_is_not_recorded(run_script, fixture_repo):
    """FR-004 / AC-006 / OT-007 — a manifest written before this change is not drift.

    RED at cfafe8e: there was no hashes key to report not_recorded with.
    """
    record(run_script, fixture_repo)
    manifest = manifest_of(fixture_repo)
    manifest.pop("lineHashes")
    write_manifest(fixture_repo, manifest)

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["hashes"] == "not_recorded", (
        f"expected hashes not_recorded for a pre-lineHashes manifest; got "
        f"{data['hashes']!r}\n{outcome(result)}"
    )
    assert data["hash_mismatches"] == [], (
        f"expected no mismatches to be invented; got {data['hash_mismatches']!r}\n"
        f"{outcome(result)}"
    )
    assert data["status"] == "clean", (
        f"expected the missing hashes to produce no drift of their own; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n{outcome(result)}"
    )


def test_no_git_note_does_not_claim_digests_compared(run_script, fixture_repo):
    """FR-004 / AC-006 / FR-009 / ST-004 — a not-checked note may not overstate the line half.

    RED at 66dab76: the four git branches built their note before ``hashes``
    existed, so each of them asserted that "the recorded line digests were
    still compared" whatever the manifest held. Against a manifest written
    before ``lineHashes`` (AC-006) nothing had been compared at all, and the
    sentence sat beside an empty ``suspect_pages`` telling the reader the line
    half had covered what the code half could not. The only clause that ever
    corrected it fired for ``partial``, so ``not_recorded`` — the one state
    where the claim is flatly false — was the state that never reached it.

    Two runs of one scenario, differing in nothing but the manifest's
    ``lineHashes`` key, so what is pinned is that the sentence is conditional
    rather than that it is worded any particular way. Both runs report no_git:
    that is the branch with no ``re-record`` of its own, so the digest sentence
    is the only thing in the note that can name the line half at all.

    RED again at a2b1e1d, in the third run below: the sentence the first run
    prints said "every resolvable anchor carried a recorded digest and was
    compared", and a citation the pages grow after the record is resolvable and
    is never compared — the loop passes over an anchor the record did not hold
    without a digest and without counting it. A tree that had grown one was
    therefore told, under a status that means half the run did not happen, that
    the half that did had covered a set wider than it read. The third run is
    the same scenario with one such citation, and the same difference property:
    the two runs may not print the same digest sentence.
    """
    record(run_script, fixture_repo)
    with_hashes = manifest_of(fixture_repo)
    assert with_hashes.get("lineHashes"), (
        f"this test needs a manifest carrying digests to strip; got {with_hashes!r}"
    )
    # The anchors half is unchanged between the two runs, and so is the tree: only the
    # manifest key moves, and check never writes the manifest back.
    shutil.rmtree(fixture_repo / ".git")

    hashed = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    hashed_data = parsed(hashed)
    write_manifest(fixture_repo, {k: v for k, v in with_hashes.items() if k != "lineHashes"})
    stripped = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    stripped_data = parsed(stripped)

    assert hashed_data["hashes"] == "checked", (
        f"this test needs the first run to have compared every digest; got "
        f"{hashed_data['hashes']!r}\n{outcome(hashed)}"
    )
    assert stripped_data["hashes"] == "not_recorded", (
        f"this test needs the second run to have had none to compare; got "
        f"{stripped_data['hashes']!r}\n{outcome(stripped)}"
    )
    assert hashed_data["status"] == stripped_data["status"] == "no_git", (
        f"expected both runs to reach the same not-checked status; got "
        f"{hashed_data['status']!r} and {stripped_data['status']!r}\n{outcome(stripped)}"
    )
    assert stripped.returncode == 2, (
        f"expected exit 2 from a check with no repository; got {stripped.returncode}\n"
        f"{outcome(stripped)}"
    )

    # Clauses rather than the whole note, because the two runs legitimately share every
    # sentence that is not about digests — the git branch that fired and the unanchored-pages
    # count are the same on both.
    hashed_clauses = set(hashed_data["note"].split("; "))
    stripped_clauses = set(stripped_data["note"].split("; "))
    assert [c for c in hashed_clauses if "digest" in c], (
        f"expected the run that did compare digests to say so; got "
        f"{hashed_data['note']!r}\n{outcome(hashed)}"
    )
    assert [c for c in stripped_clauses if "digest" in c], (
        f"expected the run that had none to compare to say that too, rather than leaving the "
        f"reader to infer it from the hashes field; got {stripped_data['note']!r}\n"
        f"{outcome(stripped)}"
    )
    shared = sorted(c for c in hashed_clauses & stripped_clauses if "digest" in c)
    assert not shared, (
        f"expected no sentence about digests to be shared by a run that compared all of them "
        f"and a run that had none recorded to compare; both notes carry {shared!r}\n"
        f"{outcome(stripped)}"
    )

    # A third run of the same scenario, differing from the first in one citation the page grew
    # after the record. It resolves, so the sentence the first run printed quantified over it;
    # the comparison loop never reached it, because the record holds no digest for an anchor it
    # did not have. What is pinned is again a difference rather than a wording: a run that
    # compared every anchor its tree has may not print the sentence a run prints when the tree
    # carries one it never recorded, or the reader of a not-checked run is told the line half
    # covered a wider set than it read.
    write_manifest(fixture_repo, with_hashes)
    faq = fixture_repo / "docs/faq.md"
    faq.write_text(
        faq.read_text(encoding="utf-8")
        + f"\nThe command line parses first. <!-- {MDX_ANCHOR} -->\n",
        encoding="utf-8",
    )

    grown = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    grown_data = parsed(grown)

    assert grown_data["status"] == "no_git" and grown_data["hashes"] == "checked", (
        f"this run needs the same not-checked status with every recorded digest compared; got "
        f"{grown_data['status']!r} / {grown_data['hashes']!r}\n{outcome(grown)}"
    )
    assert grown_data["docs_edited_since_record"], (
        f"this run needs the added citation to register as a docs edit, which is the field the "
        f"note points the reader at\n{outcome(grown)}"
    )
    grown_digest = sorted(c for c in grown_data["note"].split("; ") if "digest" in c)
    hashed_digest = sorted(c for c in hashed_clauses if "digest" in c)
    assert grown_digest != hashed_digest, (
        f"expected the digest sentence of a run whose tree grew a citation to differ from the "
        f"run that compared every anchor it had; both say {grown_digest!r}\n{outcome(grown)}"
    )
    grown_clause = " ".join(grown_digest)
    assert re.search(r"\b1 citation", grown_clause), (
        f"expected the note to say how many citations the record did not hold, one being what "
        f"this run added; got {grown_clause!r}\n{outcome(grown)}"
    )
    assert "docs_edited_since_record" in grown_clause, (
        f"expected the note to send the reader to the field that reports a citation the pages "
        f"grew, rather than leaving it as an anchor nothing measured; got {grown_clause!r}\n"
        f"{outcome(grown)}"
    )


def test_partly_hashed_manifest_is_not_reported_clean(run_script, fixture_repo):
    """FR-003 / FR-005 / FR-007 / AC-006 — `checked` has to mean every anchor.

    RED at f31b144: ``hashes`` was set to "checked" whenever lineHashes was a dict, however few
    of the anchors it held a digest for, and an anchor with no digest was skipped in silence. A
    manifest carrying a digest for one of two anchors was therefore labelled checked and
    reported clean at exit 0 with the second cited line never compared against anything.

    An anchor that resolves but was never hashed is not-measured, so this is the exit-2
    not-checked shape rather than a pass — the same refusal ``no_anchors`` already makes, and
    the stricter of the two readings the status could have taken. It sits BELOW drift, which
    the second half of this test pins: a finding the run did make is still reported as a
    finding, so "I could not check this one" never hides "I checked that one and it is wrong"
    (FR-007).

    A manifest with no lineHashes key at all is a different state and keeps reporting
    not_recorded with no drift (FR-004 / AC-006);
    ``test_manifest_without_line_hashes_is_not_recorded`` is the control for it.

    RED again at a2b1e1d, in the third stage below: the note printed the count of anchors
    carrying no digest "of len(resolvable)", and a citation the pages grew after the record is
    resolvable while the numerator can never contain it — there is no record entry for it to be
    missing a digest from. One digest removed from a record of two, in a tree that had since
    grown a third citation, read as "1 of 3 resolvable anchors" where 1 of 2 is the whole of
    what was measured, which understates the gap by whatever the pages added in between.
    """
    faq = fixture_repo / "docs/faq.md"
    faq.write_text(
        faq.read_text(encoding="utf-8")
        + f"\nThe command line parses first. <!-- {MDX_ANCHOR} -->\n",
        encoding="utf-8",
    )
    record(run_script, fixture_repo)
    manifest = manifest_of(fixture_repo)
    assert set(manifest["lineHashes"]) == {ANCHOR, MDX_ANCHOR}, (
        f"this test needs two hashed anchors to remove one of; got {manifest['lineHashes']!r}"
    )

    mdx_digest = manifest["lineHashes"][MDX_ANCHOR]
    del manifest["lineHashes"][ANCHOR]
    write_manifest(fixture_repo, manifest)

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["hashes"] == "partial", (
        "expected hashes partial when one of two resolvable anchors carries no digest; got "
        f"{data['hashes']!r}\n{outcome(result)}"
    )
    assert data["unhashed_anchors"] == [ANCHOR], (
        f"expected {ANCHOR} named as the anchor nothing compared; got "
        f"{data['unhashed_anchors']!r}\n{outcome(result)}"
    )
    assert data["status"] == "hashes_partial", (
        f"expected the not-checked status rather than a pass; got {data['status']!r}\n"
        f"{outcome(result)}"
    )
    assert result.returncode == 2, (
        "expected exit 2, never the exit 0 of a run that compared every anchor it has; got "
        f"{result.returncode}\n{outcome(result)}"
    )
    assert data["note"], (
        f"expected a note saying which half was not measured\n{outcome(result)}"
    )
    # The control for the two numbers the run below moves apart: here every anchor that resolves
    # is one the record held, so the two readings of the denominator agree at 2 and only the
    # third stage can tell them apart.
    partial_clause = [c for c in data["note"].split("; ") if "digest" in c]
    assert len(partial_clause) == 1, (
        f"expected exactly one clause of the note to be about digests; got {partial_clause!r}\n"
        f"{outcome(result)}"
    )
    assert re.search(r"\b1 of (?:the )?2\b", partial_clause[0]), (
        f"expected the note to state the gap as 1 of the 2 anchors the record held; got "
        f"{partial_clause[0]!r}\n{outcome(result)}"
    )

    # FR-007: now the anchor that WAS hashed stops matching. The finding has to win.
    manifest["lineHashes"][MDX_ANCHOR] = "0" * 16
    write_manifest(fixture_repo, manifest)

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "drift", (
        f"expected drift to outrank the partial run; got {data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n{outcome(result)}"
    )
    assert data["hash_mismatches"] == [MDX_ANCHOR], (
        f"expected {MDX_ANCHOR} under hash_mismatches; got {data['hash_mismatches']!r}\n"
        f"{outcome(result)}"
    )
    assert data["hashes"] == "partial" and data["unhashed_anchors"] == [ANCHOR], (
        "expected the partial coverage still reported alongside the finding; got "
        f"{data['hashes']!r} / {data['unhashed_anchors']!r}\n{outcome(result)}"
    )

    # The third stage: the mismatch put back to what the file says, and one citation the page
    # grew after the record. The anchor that lost its digest is one of the two the record held,
    # and the third was never in the record to be missing one — so the denominator is 2 whatever
    # the tree has grown, and a note that counts 3 states the gap over a set nothing compared.
    manifest["lineHashes"][MDX_ANCHOR] = mdx_digest
    write_manifest(fixture_repo, manifest)
    index = fixture_repo / "docs/index.md"
    index.write_text(
        index.read_text(encoding="utf-8")
        + f"\nThe parser is built here. <!-- {GROWN_ANCHOR} -->\n",
        encoding="utf-8",
    )

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["anchors"] == 3 and data["broken_anchors"] == [], (
        f"this stage needs three anchors that all resolve; got {data['anchors']} with "
        f"{data['broken_anchors']!r}\n{outcome(result)}"
    )
    assert data["status"] == "hashes_partial" and data["unhashed_anchors"] == [ANCHOR], (
        f"expected the grown citation to leave the not-checked status where it was, on the one "
        f"anchor the record held no digest for; got {data['status']!r} / "
        f"{data['unhashed_anchors']!r}\n{outcome(result)}"
    )
    grown_clause = [c for c in data["note"].split("; ") if "digest" in c]
    assert len(grown_clause) == 1, (
        f"expected exactly one clause of the note to be about digests; got {grown_clause!r}\n"
        f"{outcome(result)}"
    )
    assert re.search(r"\b1 of (?:the )?2\b", grown_clause[0]), (
        f"expected the denominator to stay at the 2 anchors the record held; got "
        f"{grown_clause[0]!r}\n{outcome(result)}"
    )
    assert not re.search(r"\bof (?:the )?3\b", grown_clause[0]), (
        f"expected the citation the page grew after the record to be counted under its own "
        f"name rather than in the denominator: it carries no digest because the record never "
        f"had one to take, which is not the same as a digest that was not compared; got "
        f"{grown_clause[0]!r}\n{outcome(result)}"
    )


def test_anchor_past_end_of_file_gets_no_line_hash(run_script, fixture_repo):
    """FR-034 — a line number past the end of the file is a broken anchor, not a hash.

    RED at cfafe8e: no lineHashes key existed at all, so there was no rule
    about what belongs in it.
    """
    faq = fixture_repo / "docs/faq.md"
    faq.write_text(
        faq.read_text(encoding="utf-8") + "\nSee the parser. <!-- src/cli/main.py:999 -->\n",
        encoding="utf-8",
    )
    printed = record(run_script, fixture_repo)
    manifest = manifest_of(fixture_repo)

    assert "src/cli/main.py:999" in manifest["anchors"], (
        f"expected the unresolvable anchor to still be recorded; got {manifest['anchors']!r}"
    )
    assert "src/cli/main.py:999" not in manifest["lineHashes"], (
        f"expected no hash for a line that does not exist; got {manifest['lineHashes']!r}"
    )
    assert printed["anchors"] == 2 and printed["lineHashes"] == 1, (
        f"expected 2 anchors and 1 hashed line; got {printed!r}"
    )

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert [b["anchor"] for b in data["broken_anchors"]] == ["src/cli/main.py:999"], (
        f"expected the anchor reported broken with its own reason; got "
        f"{data['broken_anchors']!r}\n{outcome(result)}"
    )
    assert data["hash_mismatches"] == [], (
        f"expected one failure reported once, not twice; got {data['hash_mismatches']!r}\n"
        f"{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n{outcome(result)}"
    )


def test_anchor_citing_line_zero_is_a_broken_anchor(run_script, fixture_repo):
    """FR-034 / FR-003 / FR-005 — a line number below 1 is a broken citation, not a gap.

    RED at 19941ad: ``resolves`` asked only whether the cited line was past the END of its
    file, so ``src/cli/main.py:0`` came back resolvable — while ``cited_line`` enumerates from
    1 and can never return a line 0. The anchor was resolvable and unhashable at once: record
    wrote no digest for it, check counted it under unhashed_anchors, and the set reported
    hashes_partial at exit 2 with a note saying re-record. Re-recording produced the same
    manifest and the same exit 2, so the state was permanent and nothing the author could do
    to the citation would clear it.

    Line numbers are 1-based everywhere an anchor is written, so 0 names no line and the
    citation is broken in the way ``src/app/missing.py:3`` is broken. Reported as drift it
    names the page to fix, and the invariant the hash half relies on — a line that cannot be
    hashed is a line that does not resolve — becomes true.
    """
    zero_anchor = "src/cli/main.py:0"
    faq = fixture_repo / "docs/faq.md"
    faq.write_text(
        faq.read_text(encoding="utf-8") + f"\nSee the top. <!-- {zero_anchor} -->\n",
        encoding="utf-8",
    )
    printed = record(run_script, fixture_repo)
    manifest = manifest_of(fixture_repo)

    assert zero_anchor in manifest["anchors"], (
        f"expected the citation to still be recorded; got {manifest['anchors']!r}"
    )
    assert zero_anchor not in manifest["lineHashes"], (
        f"expected no digest for a line that does not exist; got {manifest['lineHashes']!r}"
    )
    assert printed["anchors"] == 2 and printed["lineHashes"] == 1, (
        f"expected 2 anchors and 1 hashed line; got {printed!r}"
    )

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert [b["anchor"] for b in data["broken_anchors"]] == [zero_anchor], (
        f"expected {zero_anchor} reported as a broken anchor with its own reason; got "
        f"{data['broken_anchors']!r}\n{outcome(result)}"
    )
    assert data["unhashed_anchors"] == [], (
        "expected one failure reported once: an anchor that does not resolve is not also an "
        f"anchor nothing measured; got {data['unhashed_anchors']!r}\n{outcome(result)}"
    )
    assert data["status"] == "drift", (
        f"expected status drift rather than the permanent hashes_partial; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n{outcome(result)}"
    )


def test_latin1_cited_line_byte_change_is_a_mismatch(run_script, fixture_repo):
    """US-001 / FR-003 / FR-005 / ST-006 — the digest is over the bytes, not a lossy decode.

    RED at 19c97f9: ``cited_line`` opened the cited file with
    ``errors="replace"``, so every byte the UTF-8 decoder could not read became the same U+FFFD
    before ``line_hash`` encoded it back with a plain ``.encode("utf-8")``. Two latin-1 bytes
    therefore produced one digest, the recorded hash still matched, and this check printed
    status clean at exit 0 — under a user story titled "A cited file edit is never reported
    clean".

    The cited file is named in .gitignore so that git has nothing to report about it: with the
    code half silent, the assertion below is about the hash half and nothing else.
    """
    legacy = fixture_repo / LATIN1_FILE
    legacy.write_bytes(LATIN1_BEFORE)
    (fixture_repo / ".gitignore").write_text(f"{LATIN1_FILE}\n", encoding="utf-8")
    faq = fixture_repo / "docs/faq.md"
    faq.write_text(
        faq.read_text(encoding="utf-8")
        + f"\nThe loader is older than the tree. <!-- {LATIN1_ANCHOR} -->\n",
        encoding="utf-8",
    )
    # Committed before the record so the working tree is quiet when the check runs: an
    # untracked .gitignore would itself show up as a changed code file.
    git_in(fixture_repo, "add", "--", ".gitignore")
    git_in(fixture_repo, "commit", "-q", "-m", "ignore the legacy loader")

    record(run_script, fixture_repo)
    manifest = manifest_of(fixture_repo)

    assert manifest["lineHashes"].get(LATIN1_ANCHOR) == hash_of_bytes(LATIN1_BEFORE.strip()), (
        f"expected the recorded digest to be taken over the line's bytes; got "
        f"{manifest['lineHashes'].get(LATIN1_ANCHOR)!r}"
    )

    # One byte of the cited line changes: 0xe9 -> 0xe8. Nothing else in the tree moves.
    legacy.write_bytes(LATIN1_AFTER)

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["code_files_changed"] == 0, (
        "this test needs git to have nothing to say, so that only the hash half can notice "
        f"the edit; got {data['code_files_changed']} changed file(s)\n{outcome(result)}"
    )
    assert data["suspect_pages"] == {}, (
        f"expected no page suspect through the code half; got {data['suspect_pages']!r}\n"
        f"{outcome(result)}"
    )
    assert data["hash_mismatches"] == [LATIN1_ANCHOR], (
        f"expected {LATIN1_ANCHOR} under hash_mismatches after one byte of the cited line "
        f"changed; got {data['hash_mismatches']!r}\n{outcome(result)}"
    )
    assert data["status"] == "drift", (
        f"expected status drift for an edited cited line; got {data['status']!r}\n"
        f"{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1 alongside status drift; got {result.returncode}\n{outcome(result)}"
    )


# ---------------------------------------------------------------------------
# FR-005 / FR-007 / FR-008: the status vocabulary and what outranks what
# ---------------------------------------------------------------------------
def test_uncited_commit_is_unrelated_changes(run_script, fixture_repo):
    """FR-005 / AC-007 / OT-008 — code no page cites changed; that is not drift.

    RED at cfafe8e: any non-empty changed set made clean False, so an
    ordinary commit to an uncited file failed the gate at exit 1.
    """
    record(run_script, fixture_repo)
    uncited = fixture_repo / UNCITED_FILE
    uncited.write_text(uncited.read_text(encoding="utf-8") + "# unrelated\n", encoding="utf-8")
    git_in(fixture_repo, "add", "--", UNCITED_FILE)
    git_in(fixture_repo, "commit", "-q", "-m", "an unrelated change")

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "unrelated_changes", (
        f"expected status unrelated_changes for a commit to {UNCITED_FILE}; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"expected exit 0 for unrelated_changes; got {result.returncode}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 1, (
        f"expected code_files_changed 1; got {data['code_files_changed']}\n{outcome(result)}"
    )
    assert data["suspect_pages"] == {}, (
        f"expected no suspect page; got {data['suspect_pages']!r}\n{outcome(result)}"
    )
    assert data["note"], f"expected a note alongside the count\n{outcome(result)}"


def test_docs_only_edit_is_unrelated_changes(run_script, fixture_repo):
    """FR-008 / AC-008 / OT-009 — editing a page is the author's job, not drift.

    RED at cfafe8e: a docs edit changed docsHash, clean went False and the
    gate failed at exit 1 with no code change anywhere in sight.
    """
    record(run_script, fixture_repo)
    faq = fixture_repo / "docs/faq.md"
    faq.write_text(faq.read_text(encoding="utf-8") + "\nA new answer.\n", encoding="utf-8")

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "unrelated_changes", (
        f"expected status unrelated_changes for a docs-only edit; got {data['status']!r}\n"
        f"{outcome(result)}"
    )
    assert data["docs_edited_since_record"] is True, (
        f"expected docs_edited_since_record true; got {data['docs_edited_since_record']!r}\n"
        f"{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 0, (
        f"expected no code file changed; got {data['code_files_changed']}\n{outcome(result)}"
    )


def test_docs_edit_adding_a_citation_is_unrelated(run_script, fixture_repo):
    """FR-008 / ST-003 / AC-008 / OT-009 / FR-006 — a new citation is a docs edit.

    RED at 19941ad: every resolvable anchor the manifest carried no digest for went into
    ``unhashed_anchors``, and an anchor the page grew AFTER the record is one of them — there
    was nothing to take a digest of when record ran. Appending one sentence carrying a citation
    therefore moved the run to hashes_partial at exit 2 with a note telling the author to
    re-record, under a requirement reading "report docs_edited_since_record: true and status
    unrelated_changes" at exit 0. ``test_docs_only_edit_is_unrelated_changes`` above is the
    control: the same edit without a citation already passed, so the citation was the trigger.

    An anchor the record DID hold and never hashed is a different fact and still refuses to
    report a pass; ``test_partly_hashed_manifest_is_not_reported_clean`` pins that half.
    """
    record(run_script, fixture_repo)
    faq = fixture_repo / "docs/faq.md"
    faq.write_text(
        faq.read_text(encoding="utf-8")
        + f"\nThe command line parses first. <!-- {MDX_ANCHOR} -->\n",
        encoding="utf-8",
    )

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "unrelated_changes", (
        "expected status unrelated_changes for a docs edit that adds a citation; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n{outcome(result)}"
    )
    assert data["docs_edited_since_record"] is True, (
        f"expected docs_edited_since_record true; got {data['docs_edited_since_record']!r}\n"
        f"{outcome(result)}"
    )
    assert data["unhashed_anchors"] == [], (
        "expected a citation added after the record to be reported as the docs edit it is, "
        f"not as an anchor nothing measured; got {data['unhashed_anchors']!r}\n"
        f"{outcome(result)}"
    )
    assert data["code_files_changed"] == 0, (
        f"expected no code file changed; got {data['code_files_changed']}\n{outcome(result)}"
    )


def test_broken_anchor_outranks_unrelated_churn(run_script, fixture_repo):
    """FR-007 / AC-009 — a broken anchor wins over an exit-0 observation.

    RED at cfafe8e: the hashes key the last assertion reads did not exist.
    """
    faq = fixture_repo / "docs/faq.md"
    faq.write_text(
        faq.read_text(encoding="utf-8") + "\nSee the loader. <!-- src/app/missing.py:3 -->\n",
        encoding="utf-8",
    )
    record(run_script, fixture_repo)
    uncited = fixture_repo / UNCITED_FILE
    uncited.write_text(uncited.read_text(encoding="utf-8") + "# unrelated\n", encoding="utf-8")
    git_in(fixture_repo, "add", "--", UNCITED_FILE)
    git_in(fixture_repo, "commit", "-q", "-m", "an unrelated change")

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "drift", (
        "expected the broken anchor to outrank unrelated churn; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n{outcome(result)}"
    )
    assert [b["anchor"] for b in data["broken_anchors"]] == ["src/app/missing.py:3"], (
        f"expected the missing file reported; got {data['broken_anchors']!r}\n{outcome(result)}"
    )
    assert data["code_files_changed"] == 1, (
        f"expected the churn still counted; got {data['code_files_changed']}\n{outcome(result)}"
    )
    assert data["hashes"] == "checked", (
        f"expected the hash half to have run; got {data['hashes']!r}\n{outcome(result)}"
    )


def test_nothing_changed_since_record_is_clean(run_script, fixture_repo):
    """FR-005 / AC-010 / ST-006 — clean is reserved for zero changes.

    The status and exit assertions here read the same before and after,
    deliberately: this is the control that stops the unrelated_changes and drift
    tests above from passing by declaring everything a finding. What made it RED
    is the hashes assertion — the cfafe8e envelope had no such key.
    """
    record(run_script, fixture_repo)

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "clean", (
        f"expected status clean straight after a record; got {data['status']!r}\n"
        f"{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"expected exit 0; got {result.returncode}\n{outcome(result)}"
    )
    assert data["hashes"] == "checked", (
        f"expected the recorded hashes to have been compared; got {data['hashes']!r}\n"
        f"{outcome(result)}"
    )
    assert data["code_files_changed"] == 0 and data["docs_edited_since_record"] is False, (
        f"expected nothing changed; got {data!r}\n{outcome(result)}"
    )


def test_docs_directory_with_no_pages_is_not_clean(run_script, fixture_repo):
    """FR-005 / FR-006 / CT-001 / ST-006 — zero pages scanned is not a pass.

    RED at 19c97f9: ``nothing_to_measure = not anchors and paths``
    required the page list to be NON-empty, so a docs tree holding no page at all fell past
    the no_anchors sentinel to ``clean`` at exit 0. Nothing was scanned, nothing was resolved,
    and the gate reported a pass — the count of what was skipped standing in for the count of
    what was checked.
    """
    shutil.rmtree(fixture_repo / "docs")
    (fixture_repo / "docs").mkdir()

    printed = record(run_script, fixture_repo)
    assert printed["pages"] == 0 and printed["anchors"] == 0, (
        f"this test needs an empty docs tree; record saw {printed!r}"
    )

    result = run_script("drift.py", "check", "docs", cwd=fixture_repo)
    data = parsed(result)

    assert data["status"] == "no_anchors", (
        f"expected the not-checked sentinel for a docs tree with no pages; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 2, (
        f"expected exit 2, never the exit 0 of a pass; got {result.returncode}\n"
        f"{outcome(result)}"
    )
    assert data["pages"] == 0 and data["anchors"] == 0, (
        f"expected the empty counts reported back; got {data!r}\n{outcome(result)}"
    )
    assert data["note"], (
        f"expected a note saying why nothing could be measured\n{outcome(result)}"
    )


def test_md_page_citing_a_changed_file_is_drift(run_script, fixture_repo):
    """FR-005 / ST-001 — the control for the .mdx twin below.

    Green at 19c97f9 and green now, deliberately: it is what makes the twin's failure a
    statement about the extension rather than about this scenario. The page, the citation and
    the edit are byte-identical in both tests.
    """
    page, result, data = record_then_touch_uncited(run_script, fixture_repo, ".md")

    assert data["status"] == "drift", (
        f"expected status drift when a .md page cites the file that changed; got "
        f"{data['status']!r}\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1; got {result.returncode}\n{outcome(result)}"
    )
    assert data["suspect_pages"].get(page) == [MDX_ANCHOR], (
        f"expected {page} suspect through {MDX_ANCHOR}; got {data['suspect_pages']!r}\n"
        f"{outcome(result)}"
    )


def test_mdx_page_citing_a_changed_file_is_drift(run_script, fixture_repo):
    """FR-005 / FR-006 / ST-006 — a .mdx page is a page.

    RED at 19c97f9: ``doc_files`` kept only names ending ``.md`` while
    ``.mdx`` sits in ``SRC_EXT`` as a citable file, so a Docusaurus set
    written in .mdx was never scanned. This check reported unrelated_changes at exit 0 with a
    note saying no page cites the file that had just changed — with the page sitting in the
    tree, citing it. The .md control above runs the identical scenario one character apart.
    """
    page, result, data = record_then_touch_uncited(run_script, fixture_repo, ".mdx")

    assert page in data["suspect_pages"], (
        f"expected the .mdx page to be scanned and reported suspect; got "
        f"{data['suspect_pages']!r}\n{outcome(result)}"
    )
    assert data["suspect_pages"][page] == [MDX_ANCHOR], (
        f"expected {page} suspect through {MDX_ANCHOR}; got {data['suspect_pages']!r}\n"
        f"{outcome(result)}"
    )
    assert data["status"] == "drift", (
        f"expected the same answer the .md twin gets; got {data['status']!r}\n"
        f"{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"expected exit 1, not the exit 0 an unscanned page produced; got "
        f"{result.returncode}\n{outcome(result)}"
    )
    assert "no page cites" not in data["note"], (
        f"expected no claim that nothing cites the changed file; got {data['note']!r}\n"
        f"{outcome(result)}"
    )


# ---------------------------------------------------------------------------
# FR-009: record in a repository that has no HEAD to record
# ---------------------------------------------------------------------------
def test_record_without_a_commit_writes_a_null_head(run_script, fixture_repo, tmp_path):
    """FR-009 / OT-010 — gitHead null plus a note, and the next check says no_git.

    RED at cfafe8e: record wrote ``"gitHead": ""`` with no note, and check
    read that empty string as "nothing to compare against" and printed clean.
    """
    fresh = tmp_path / "fresh"
    shutil.copytree(fixture_repo, fresh, ignore=shutil.ignore_patterns(".git"))
    git_in(fresh, "-c", "init.defaultBranch=main", "init", "-q", ".")

    printed = record(run_script, fresh)

    assert printed["gitHead"] is None, (
        f"expected a JSON null gitHead, not an empty string; got {printed['gitHead']!r}"
    )
    assert printed["note"], f"expected a note saying why there is no head; got {printed!r}"
    assert manifest_of(fresh)["gitHead"] is None, (
        f"expected the manifest to record null too; got {manifest_of(fresh)['gitHead']!r}"
    )

    result = run_script("drift.py", "check", "docs", cwd=fresh)
    data = parsed(result)

    assert data["status"] == "no_git", (
        f"expected the following check to report no_git; got {data['status']!r}\n"
        f"{outcome(result)}"
    )
    assert result.returncode == 2, (
        f"expected exit 2; got {result.returncode}\n{outcome(result)}"
    )


def test_null_head_in_the_manifest_reports_no_git(run_script, fixture_repo, tmp_path):
    """FR-005 / FR-009 / OT-010 — a manifest that records no commit is not clean.

    RED at 9859317: status clean at exit 0, with an empty note. Both git
    branches in the check were guarded by ``if recorded and ...``, so a manifest
    holding ``"gitHead": null`` fell past both of them: nothing measured the code
    half, git_note stayed empty, and the envelope came back saying nothing had
    changed — with git working and a commit sitting right there that the pages
    were never compared against. ``NO_HEAD_NOTE`` promises the reader of that
    record exactly the opposite: the next check "reports no_git for the one half
    it has no commit to start the diff from".

    ``test_record_without_a_commit_writes_a_null_head`` cannot catch this. That
    repository still has no commits when its check runs, so ``head_sha()``
    returns None and the run reaches no_git down the other branch. The commit
    made below is the whole difference: git can answer now, and the only thing
    missing is the head the manifest never recorded.
    """
    fresh = tmp_path / "fresh"
    shutil.copytree(fixture_repo, fresh, ignore=shutil.ignore_patterns(".git"))
    git_in(fresh, "-c", "init.defaultBranch=main", "init", "-q", ".")

    recorded_head = record(run_script, fresh)["gitHead"]
    assert recorded_head is None, (
        f"this test needs a manifest with no recorded commit; got {recorded_head!r}"
    )

    git_in(fresh, "add", "-A")
    git_in(fresh, "commit", "-q", "-m", "first")

    result = run_script("drift.py", "check", "docs", cwd=fresh)
    data = parsed(result)

    assert data["status"] == "no_git", (
        f"expected no_git for a manifest recording no commit; got {data['status']!r}\n"
        f"{outcome(result)}"
    )
    assert result.returncode == 2, (
        f"expected exit 2 for a check that had nothing to compare against; got "
        f"{result.returncode}\n{outcome(result)}"
    )
    assert data["note"], (
        f"expected a note saying why the code half was not measured\n{outcome(result)}"
    )
    assert data["gitHead"] == {"recorded": None, "current": data["gitHead"]["current"]}, (
        f"expected the null recorded head reported back beside the resolvable current one; "
        f"got {data['gitHead']!r}\n{outcome(result)}"
    )
    assert data["gitHead"]["current"], (
        f"this test needs git answering by now; got {data['gitHead']!r}\n{outcome(result)}"
    )
