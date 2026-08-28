"""CLI tests for plugins/webster/scripts/drift.py (US-001, US-002, US-010).

Every test drives the script through the conftest ``run_script`` helper as a
subprocess (GI-004, CT-007). Nothing here imports drift.py: it resolves ROOT,
the docs directory and the manifest path at module import (``drift.py:20-23``),
so an import would freeze the wrong repository before a test could choose one.

Which fix each test pins, and how it failed on the pre-change script — the RED
baseline required by FR-039 / AC-039, verified against ``drift.py`` at commit
2abe081, and for the last two rows — the pair a GRIND cycle found still
false-passing after that commit — against ``drift.py`` at commit 9859317:

===================================================  ==============  ===========================
test                                                 pins            was, before the fix
===================================================  ==============  ===========================
test_uncommitted_edit_to_cited_file_is_drift         FR-001 AC-001   status clean, exit 0
test_staged_rename_marks_old_and_new_path_dirty      FR-001 AC-002   suspect_pages {}, count 0
test_check_outside_a_git_repo_reports_no_git         FR-002 AC-003   status drift, exit 1
test_rebased_away_head_reports_head_missing          FR-037 AC-004   status clean, exit 0
test_record_writes_a_line_hash_per_anchor            FR-003 OT-005   no lineHashes key at all
test_changed_cited_line_is_reported_as_a_mismatch    FR-003 AC-005   no hash_mismatches key
test_hash_mismatch_alone_is_drift                    FR-007          status clean, exit 0
test_manifest_without_line_hashes_is_not_recorded    FR-004 AC-006   no hashes key
test_uncited_commit_is_unrelated_changes             FR-005 AC-007   status drift, exit 1
test_docs_only_edit_is_unrelated_changes             FR-008 AC-008   status drift, exit 1
test_broken_anchor_outranks_unrelated_churn          FR-007 AC-009   no hashes key
test_nothing_changed_since_record_is_clean           FR-005 AC-010   no hashes key
test_record_without_a_commit_writes_a_null_head      FR-009 OT-010   gitHead "", no note
test_anchor_past_end_of_file_gets_no_line_hash       FR-034          no lineHashes key at all
test_non_ascii_path_under_a_c_locale_still_answers   FR-002 FR-037   traceback, no JSON, exit 1
test_null_head_in_the_manifest_reports_no_git        FR-005 FR-009   status clean, exit 0
===================================================  ==============  ===========================

The exit map under test is FR-006: clean and unrelated_changes 0, drift 1,
no_docs / no_manifest / no_anchors / no_git / head_missing 2.

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


def git_in(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run one git command inside ``repo`` with a fixed identity and no user config.

    Tests mutate their repository — edit, rename, commit, rebase away — and the
    mutation is the premise of the assertion that follows, so ``check=True`` by
    default makes a broken premise an error at that line instead of a confusing
    failure three lines later.
    """
    env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    env.update(GIT_ENV)
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


# ---------------------------------------------------------------------------
# FR-001: git status --porcelain -z, both halves of a rename
# ---------------------------------------------------------------------------
def test_uncommitted_edit_to_cited_file_is_drift(run_script, fixture_repo):
    """FR-001 / AC-001 / OT-001 — the false clean this casting exists to stop.

    RED before the fix: ``git()`` stripped the output of ``git status --short``,
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

    RED before the fix: ``R  src/app/main.py -> src/app/server.py`` was sliced to
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


# ---------------------------------------------------------------------------
# FR-002 / FR-006 / FR-037: git failures are statuses, not empty strings
# ---------------------------------------------------------------------------
def test_check_outside_a_git_repo_reports_no_git(run_script, fixture_repo):
    """FR-002 / FR-006 / AC-003 / OT-003 — no repository is exit 2, anchors still reported.

    RED before the fix: ``git()`` swallowed the failure into "", so the check
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

    RED before the fix: ``git diff --name-only OLD..HEAD`` exits 128 when OLD is
    gone, ``git()`` turned that into "", changed came back empty and the check
    printed clean at exit 0.

    ``reset --hard`` alone is not enough to reproduce it: the commit survives in
    the reflog and ``rev-parse --verify`` still finds it. Expiring the reflog and
    pruning is what actually rewrites history out of the object store, and the
    assertion below refuses to run the check until that has demonstrably
    happened.
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

    RED before the fix: stderr carried a UnicodeDecodeError traceback and the
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


# ---------------------------------------------------------------------------
# FR-003 / FR-004 / FR-034: the per-anchor line hash
# ---------------------------------------------------------------------------
def test_record_writes_a_line_hash_per_anchor(run_script, fixture_repo):
    """FR-003 / OT-005 / CT-002 — lineHashes is a top-level 16-hex map at record.

    RED before the fix: the manifest had no lineHashes key, so a cited line could
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

    RED before the fix: there was no hash_mismatches key to hold the answer, so
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

    RED before the fix: status clean, exit 0.
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

    RED before the fix: there was no hashes key to report not_recorded with.
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


def test_anchor_past_end_of_file_gets_no_line_hash(run_script, fixture_repo):
    """FR-034 — a line number past the end of the file is a broken anchor, not a hash.

    RED before the fix: no lineHashes key existed at all, so there was no rule
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


# ---------------------------------------------------------------------------
# FR-005 / FR-007 / FR-008: the status vocabulary and what outranks what
# ---------------------------------------------------------------------------
def test_uncited_commit_is_unrelated_changes(run_script, fixture_repo):
    """FR-005 / AC-007 / OT-008 — code no page cites changed; that is not drift.

    RED before the fix: any non-empty changed set made clean False, so an
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

    RED before the fix: a docs edit changed docsHash, clean went False and the
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


def test_broken_anchor_outranks_unrelated_churn(run_script, fixture_repo):
    """FR-007 / AC-009 — a broken anchor wins over an exit-0 observation.

    RED before the fix: the hashes key the last assertion reads did not exist.
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
    is the hashes assertion — the pre-change envelope had no such key.
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


# ---------------------------------------------------------------------------
# FR-009: record in a repository that has no HEAD to record
# ---------------------------------------------------------------------------
def test_record_without_a_commit_writes_a_null_head(run_script, fixture_repo, tmp_path):
    """FR-009 / OT-010 — gitHead null plus a note, and the next check says no_git.

    RED before the fix: record wrote ``"gitHead": ""`` with no note, and check
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

    RED before the fix: status clean at exit 0, with an empty note. Both git
    branches in the check were guarded by ``if recorded and ...``
    (``drift.py:262,266``), so a manifest holding ``"gitHead": null`` fell past
    both of them: nothing measured the code half, git_note stayed empty, and the
    envelope came back saying nothing had changed — with git working and a
    commit sitting right there that the pages were never compared against.
    NO_HEAD_NOTE (``drift.py:32-33``) promises the reader of that record exactly
    the opposite: "the next check reports no_git instead of comparing the pages
    against nothing".

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
