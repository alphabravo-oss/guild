"""conftest for foundry-mcp-server tests (Phase 4 / EVID-01).

Mirrors plugins/forge/tests/conftest.py:90-114 helper-fixture shape verbatim.
PLUGIN_ROOT resolves to plugins/foundry/mcp-server/ (parent of this conftest).

Three fixtures:

- ``fixtures_dir``: session-scoped path to ``tests/fixtures/``
- ``load_fixture``: function returning the text content of a fixture file
- ``run_accept_casting_with_evidence``: end-to-end harness — synthesizes a
  tiny git repo with a casting commit + evidence file, invokes
  ``verify_evidence`` directly (Plan 04-03 — Plan 04-04 wraps with
  ``foundry_accept_casting`` + v2.0 stream-skip routing). Mirrors Plan
  03-01's ``run_f05_decompose_with_test_roster`` precedent: signature
  locked in Plan 04-01; body lands here in Plan 04-03 (Rule-3 deviation
  from CONTEXT.md's "Plan 04-04 lands the harness" prose, mirroring Phase 3
  Plan 03-02's "test_unknown_version_hard_fails turned GREEN one wave
  early" precedent — when the wave's helpers are sufficient to turn a
  test green, do not artificially defer).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

import pytest


# ``conftest.py`` lives at ``plugins/foundry/mcp-server/tests/conftest.py`` so
# the MCP-server plugin root is the parent of the tests directory.
PLUGIN_ROOT = Path(__file__).resolve().parent.parent  # plugins/foundry/mcp-server/
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    """Path to the fixtures directory shared by all tests."""
    return FIXTURES_DIR


@pytest.fixture
def load_fixture(fixtures_dir: Path) -> Callable[[str], str]:
    """Return a callable that reads ``fixtures_dir / name`` and returns text.

    Raises ``FileNotFoundError`` with an explicit "Wave 0 plan didn't create it"
    diagnostic so a missing fixture in a downstream wave is easy to spot.
    """

    def _loader(name: str) -> str:
        target = fixtures_dir / name
        if not target.is_file():
            raise FileNotFoundError(
                f"fixture missing: {target} — "
                f"Wave 0 plan (04-01) didn't create it. "
                f"Re-run plan 04-01 (Wave 0 scaffolding) to regenerate fixtures."
            )
        return target.read_text(encoding="utf-8")

    return _loader


# ---------------------------------------------------------------------------
# run_accept_casting_with_evidence harness — Plan 04-03 body.
#
# Synthesizes a deterministic mini-repo for each test invocation so
# ``verify_evidence`` can re-execute the cmd inside an isolated worktree.
# Re-execution-must-byte-match scenarios rewrite the cmd to ``cat body.txt``
# where ``body.txt`` carries the committed body; mismatch scenarios tweak
# body.txt to differ. Pitfall 4/5/etc. tested via dedicated scenarios.
# ---------------------------------------------------------------------------


# ``# evidence-cmd:`` line matcher — captures the cmd suffix so the harness
# can rewrite the cmd while preserving the original (e.g., for the
# evidence_log_timeout.log fixture which needs ``sleep 999`` left intact).
_HEADER_CMD_RE = re.compile(r"^# evidence-cmd:\s*(.+)$", re.MULTILINE)
_HEADER_BLOCK_RE = re.compile(r"\A(?:#[^\n]*\n|[ \t]*\n)+")

# Phase 5 / Plan 05-01 — ``# evidence-for:`` directive matcher. Used by
# ``_apply_evidence_for_directive`` (below) to override or strip the for-line
# at synth-time per harness kwargs. Parser-side recognition lands in Plan
# 05-02 (extends ``_KNOWN_HEADER_DIRECTIVES`` with ``"for"``). The harness's
# rewrite layer is independent of the parser allowlist so Plans 05-01/02 can
# land in either order.
_HEADER_FOR_RE = re.compile(r"^# evidence-for:\s*(.+)$", re.MULTILINE)


def _apply_evidence_for_directive(
    evidence_text: str,
    *,
    evidence_for_value: str | None,
    omit_evidence_for_header: bool,
) -> str:
    """Return evidence_text with the ``# evidence-for:`` line rewritten.

    Phase 5 / Plan 05-01 helper. Three branches in priority order:

      1. ``omit_evidence_for_header=True`` — strip any ``# evidence-for:``
         line from the header block. Drives EVIDENCE_REQUIREMENT_UNBOUND
         once Plan 05-03 lands the gate.
      2. ``evidence_for_value`` is not None — replace any existing for-line
         with ``# evidence-for: {evidence_for_value}``. If no for-line
         exists, insert one immediately after the ``# evidence-cmd:`` line.
      3. Both at defaults — return ``evidence_text`` unchanged.

    The rewrite operates on the fixture text BEFORE it lands in the synth
    repo, so ``_rewrite_evidence_for_scenario`` (which rewrites the
    ``# evidence-cmd:`` line for cat-replay) can run after this transform
    without colliding.
    """
    if omit_evidence_for_header:
        # Strip the for-line entirely; leave a blank line if nothing else
        # remains in the header so the header→body split stays predictable.
        return _HEADER_FOR_RE.sub("", evidence_text, count=1).replace(
            "\n\n\n", "\n\n", 1
        )
    if evidence_for_value is not None:
        replacement = f"# evidence-for: {evidence_for_value}"
        if _HEADER_FOR_RE.search(evidence_text):
            return _HEADER_FOR_RE.sub(
                lambda _m: replacement, evidence_text, count=1
            )
        # No existing for-line — insert one immediately after evidence-cmd.
        cmd_match = _HEADER_CMD_RE.search(evidence_text)
        if cmd_match is None:
            # Fixture has no cmd line either; prepend the for-line at top.
            return f"{replacement}\n{evidence_text}"
        insert_at = cmd_match.end()
        return (
            evidence_text[:insert_at]
            + f"\n{replacement}"
            + evidence_text[insert_at:]
        )
    return evidence_text


def _split_header_and_body(evidence_text: str) -> tuple[str, str]:
    """Split evidence text into (header_block, body)."""
    m = _HEADER_BLOCK_RE.match(evidence_text)
    if m is None:
        return "", evidence_text
    return m.group(0), evidence_text[m.end() :]


def _rewrite_evidence_for_scenario(
    evidence_text: str,
    *,
    force_exit_code: int | None = None,
    inject_non_utf8: bool = False,
    use_cat_replay: bool = True,
    replay_file_name: str = "replay.txt",
) -> tuple[str, str]:
    """Return ``(rewritten_evidence_text, replay_content)``.

    For ``use_cat_replay`` scenarios: the rewritten evidence file's
    ``# evidence-cmd:`` becomes ``cat replay.txt``, and ``replay.txt``
    holds the FULL rewritten evidence (so ``cat replay.txt`` emits
    bytes identical to the committed evidence file → byte-match passes).

    For ``force_exit_code`` / ``inject_non_utf8`` scenarios the cmd is
    rewritten to a fixed shape; comparison won't be reached (non-zero
    exit / non-UTF-8 paths short-circuit before the comparator).

    Replay file lives at worktree-root (project_root / replay.txt) — NOT
    inside ``evidence/`` — so the cmd's cwd (worktree root) finds it via
    relative path ``cat replay.txt``.
    """
    header, body = _split_header_and_body(evidence_text)
    # ``re.sub`` interprets ``\x``/``\g``/``\1`` etc. inside the replacement
    # template, so we use a callable replacement to pass the literal cmd
    # string verbatim (otherwise ``printf '\xff'`` triggers
    # ``re.PatternError: bad escape \x``).
    def _replace_cmd_line(new_cmd_str: str) -> Callable[[re.Match], str]:
        def _r(_m: re.Match) -> str:
            return f"# evidence-cmd: {new_cmd_str}"
        return _r

    if force_exit_code is not None:
        # Force a non-zero exit BEFORE printing; comparison won't run on
        # non-zero exit so replay content is irrelevant. Caller may still
        # write replay.txt for symmetry; harness skips that for clarity.
        new_cmd = f"exit {force_exit_code}"
        rewritten_header = _HEADER_CMD_RE.sub(
            _replace_cmd_line(new_cmd), header, count=1
        )
        return rewritten_header + body, ""
    if inject_non_utf8:
        # Emit a non-UTF-8 byte (0xFF) then exit 0; comparator survives via
        # errors='replace'. Comparison may fail (mismatch), which is
        # acceptable per the test contract.
        new_cmd = "printf '\\xff'"
        rewritten_header = _HEADER_CMD_RE.sub(
            _replace_cmd_line(new_cmd), header, count=1
        )
        return rewritten_header + body, ""
    if use_cat_replay:
        new_cmd = f"cat {replay_file_name}"
        rewritten_header = _HEADER_CMD_RE.sub(
            _replace_cmd_line(new_cmd), header, count=1
        )
        # Prepend a synthetic body-line that carries the cmd-first-token
        # ("cat") so ``_is_stub_pattern_no_cmd_in_header`` doesn't fire on
        # the synth replay path. Real fixtures (e.g., a real pytest log)
        # would naturally have the cmd-token in the first 3 body lines via
        # ``pytest test session starts``; the synth ``cat replay.txt`` cmd
        # needs an explicit anchor. Both committed log AND replay file get
        # the same prefix → byte-match succeeds.
        anchor_line = f"[replay] cat {replay_file_name}\n"
        body_with_anchor = anchor_line + body
        full_rewritten = rewritten_header + body_with_anchor
        return full_rewritten, full_rewritten
    return evidence_text, ""


def _git(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess:
    """Wrapper around ``subprocess.run`` for git commands inside ``cwd``."""
    return subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
        timeout=30,
    )


def _build_synth_project(
    project_root: Path,
    casting_id: int,
    fixture_text: str,
    fixture_basename: str,
    spec_format_version: str,
    *,
    force_exit_code: int | None = None,
    inject_non_utf8: bool = False,
    use_cat_replay: bool = True,
    omit_required_evidence: bool = False,
    evidence_for_value: str | None = None,
    omit_evidence_for_header: bool = False,
    extra_evidence_fixtures: tuple[str, ...] = (),
    extra_evidence_texts: tuple[str, ...] = (),
    casting_req_ids_override: list[str] | None = None,
) -> str:
    """Build the synth project, return casting_commit SHA.

    Layout:
        project_root/
          .git/
          specs/spec.md         (frontmatter: spec_format_version=...)
          castings/manifest.json
          evidence/
            casting-{id}-{name}.log    (rewritten evidence file)
            body.txt                   (cat-replay body content)
    """
    project_root.mkdir(parents=True, exist_ok=True)

    # specs/spec.md
    spec_path = project_root / "specs" / "spec.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        f"---\nspec_format_version: {spec_format_version}\n---\n"
        f"# Synthesized spec for testing\n",
        encoding="utf-8",
    )

    # castings/manifest.json
    manifest_path = project_root / "castings" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        '{"castings": [{"id": "' + str(casting_id) + '", "evidence_provenance": []}]}\n',
        encoding="utf-8",
    )

    # Plan 05-01 / Plan 05-03: synthesize casting prompt with <spec_requirements>
    # block ONLY when ``casting_req_ids_override`` is non-None.
    #
    # Why conditional (Plan 05-03 Edit 5): once Phase 5's gate fires at
    # ``foundry_accept_casting``, the gate runs whenever ``casting_req_ids``
    # is non-empty. Phase 4 fixtures don't carry ``# evidence-for:`` headers,
    # so a default ["US-1", "FR-2"] block would force Phase 4 tests through
    # the unbound-rejection path and break their byte-equivalent guarantees.
    #
    # Plan 05-01 originally synthesized this unconditionally with default
    # IDs matching evidence_log_for_clean.log; Plan 05-03 tightens to
    # "explicit-override-only" so:
    #   - Phase 4 tests (no override) → no <spec_requirements> block →
    #     casting_req_ids parsed from a missing block is empty →
    #     gate bypassed (zero-req casting bypass clause)
    #   - Phase 5 integration tests (override set) → block written →
    #     gate runs → enforcement path verified
    #
    # The corresponding ``casting-{id}-prompt.md`` (with dash, matching
    # ``foundry_accept_casting``'s expected filename pattern) lands at the
    # active foundry-archive run dir during the integration-dispatch path
    # (see ``run_accept_casting_with_evidence._invoke_once`` below).
    if casting_req_ids_override is not None:
        casting_prompt_path = (
            project_root / "castings" / f"casting-{casting_id}.prompt.md"
        )
        spec_req_lines = "\n".join(
            f"- {rid}: synthesized requirement for testing"
            for rid in casting_req_ids_override
        )
        casting_prompt_path.write_text(
            "<spec_requirements>\n"
            f"{spec_req_lines}\n"
            "</spec_requirements>\n",
            encoding="utf-8",
        )

    if not omit_required_evidence:
        # evidence/casting-N-name.log lives in evidence/ so verify_evidence's
        # ``evidence_dir.glob('casting-{id}-*.log')`` finds it. The replay
        # file (when used) lives at worktree-root so ``cat replay.txt``
        # works with cmd's cwd = worktree root.
        evidence_dir = project_root / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        # Plan 05-01: apply ``# evidence-for:`` directive rewriting BEFORE
        # the cat-replay rewrite so both transforms compose cleanly.
        primary_text = _apply_evidence_for_directive(
            fixture_text,
            evidence_for_value=evidence_for_value,
            omit_evidence_for_header=omit_evidence_for_header,
        )
        rewritten, replay_text = _rewrite_evidence_for_scenario(
            primary_text,
            force_exit_code=force_exit_code,
            inject_non_utf8=inject_non_utf8,
            use_cat_replay=use_cat_replay,
        )
        evidence_log_path = evidence_dir / f"casting-{casting_id}-{fixture_basename}.log"
        evidence_log_path.write_text(rewritten, encoding="utf-8")
        if replay_text:
            replay_path = project_root / "replay.txt"
            replay_path.write_text(replay_text, encoding="utf-8")

        # Plan 05-01: extra evidence fixtures — copy each into evidence/
        # under deterministic ``casting-{id}-extra-{idx}.log`` naming.
        # Each extra goes through the same cat-replay pipeline as the
        # primary; the replay files use ``replay-extra-{idx}.txt`` so they
        # don't collide with the primary's ``replay.txt``. Multi-fixture
        # scenarios use distinct fixtures per casting; the primary's
        # ``cat replay.txt`` and each extra's ``cat replay-extra-N.txt``
        # all byte-match independently.
        for idx, extra_text in enumerate(extra_evidence_texts, start=1):
            extra_replay_name = f"replay-extra-{idx}.txt"
            extra_after_for = _apply_evidence_for_directive(
                extra_text,
                evidence_for_value=None,  # extras keep their native for-line
                omit_evidence_for_header=False,
            )
            extra_rewritten, extra_replay_text = _rewrite_evidence_for_scenario(
                extra_after_for,
                force_exit_code=None,
                inject_non_utf8=False,
                use_cat_replay=True,
                replay_file_name=extra_replay_name,
            )
            extra_log_path = (
                evidence_dir / f"casting-{casting_id}-extra-{idx}.log"
            )
            extra_log_path.write_text(extra_rewritten, encoding="utf-8")
            if extra_replay_text:
                (project_root / extra_replay_name).write_text(
                    extra_replay_text, encoding="utf-8"
                )

    # git init + commit
    _git(["init", "-q", "."], cwd=project_root)
    _git(["config", "user.email", "test@example.com"], cwd=project_root)
    _git(["config", "user.name", "Test"], cwd=project_root)
    _git(["add", "."], cwd=project_root)
    _git(
        ["commit", "-q", "-m", f"casting-{casting_id} synth"],
        cwd=project_root,
    )
    head = _git(["rev-parse", "HEAD"], cwd=project_root)
    return head.stdout.strip()


def _seed_orphan_worktree(project_root: Path, run_dir: Path) -> int:
    """Create a real worktree, then orphan it (delete the dir but leave
    the metadata under ``.git/worktrees/``). After ``git worktree prune``
    runs, the metadata count drops by one. Returns the count of orphans
    seeded (always 1 for this harness).
    """
    orphan_dir = run_dir / "worktrees" / "casting-orphan"
    orphan_dir.parent.mkdir(parents=True, exist_ok=True)
    head = _git(["rev-parse", "HEAD"], cwd=project_root)
    head_sha = head.stdout.strip()
    _git(
        [
            "worktree",
            "add",
            "--detach",
            str(orphan_dir),
            head_sha,
        ],
        cwd=project_root,
    )
    # Delete the worktree dir without cleanup so the .git/worktrees/
    # metadata is left dangling — exactly the prior-crash signature
    # ``_prune_orphaned_worktrees`` is meant to repair.
    import shutil as _shutil

    _shutil.rmtree(orphan_dir, ignore_errors=True)
    return 1


def _count_worktree_metadata_dirs(project_root: Path) -> int:
    """Return the number of worktree metadata subdirs under
    ``project_root/.git/worktrees/``."""
    wt_dir = project_root / ".git" / "worktrees"
    if not wt_dir.exists():
        return 0
    return sum(1 for child in wt_dir.iterdir() if child.is_dir())


@pytest.fixture
def run_accept_casting_with_evidence(tmp_path, fixtures_dir):
    """End-to-end harness for Plan 04-03 / 04-04 evidence verification tests.

    Invokes ``verify_evidence(casting_id, project_root, casting_commit,
    run_dir=...)`` against a synthesized mini-repo where the casting
    commit's worktree contains the supplied fixture (rewritten so
    re-execution byte-matches by default). Returns a dict shaped to
    satisfy both Plan 04-03 territory tests (verdict / failure_token /
    provenance) and partial Plan 04-04 territory tests (manifest fields
    populated where the harness can synthesize them; v2.0 routing /
    F0.9 7k diagnostics still ``pytest.skip`` because Plan 04-04
    lands those code paths).

    Recognized kwargs:
      casting_id (int): default 1.
      casting_commit (str | None): explicit commit override; when None,
          harness uses HEAD of the synthesized repo.
      spec_format_version (str): default "v2.1". v2.0 → ``pytest.skip``
          (Plan 04-04 territory).
      force_exit_code (int): re-exec ``exit N`` instead of cat-replay
          → EVIDENCE_EXIT_NONZERO with ``provenance.exit_code == N``.
      seed_orphan_worktree (bool): pre-seed an orphan worktree so the
          first ``_prune_orphaned_worktrees`` call cleans it; the
          harness reports the cleanup count in
          ``manifest['orphan_worktrees_pruned']``.
      inject_non_utf8 (bool): re-exec ``printf '\\xff'`` so the
          comparator's ``errors='replace'`` path is exercised.
      concurrent_invocations (int): spawn N parallel verify_evidence
          calls on the same project_root; threading.Lock serializes them.
      omit_required_evidence (bool): commit no evidence files (Plan
          04-04 F0.9 7k territory) → ``pytest.skip``.
      force_orphaned_commit (bool): pass an unresolvable commit hash
          → EVIDENCE_COMMIT_MISSING.
    """

    def _run(
        evidence_fixture: str,
        *,
        casting_id: int = 1,
        casting_commit: str | None = None,
        spec_format_version: str = "v2.1",
        force_exit_code: int | None = None,
        seed_orphan_worktree: bool = False,
        inject_non_utf8: bool = False,
        concurrent_invocations: int = 1,
        omit_required_evidence: bool = False,
        force_orphaned_commit: bool = False,
        # Plan 05-01 — Phase 5 / EVID-02 evidence-for kwargs (locked here;
        # Plans 05-02/03 consume them).
        evidence_for_value: str | None = None,
        omit_evidence_for_header: bool = False,
        extra_evidence_fixtures: tuple[str, ...] = (),
        casting_req_ids_override: list[str] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        from foundry_mcp.tools.evidence import verify_evidence

        # Plan 04-04: v2.0 routing + omit_required_evidence paths now landed
        # in production code; harness no longer pytest.skip()s these.

        # Read fixture content.
        fixture_path = fixtures_dir / evidence_fixture
        if not fixture_path.is_file():
            raise FileNotFoundError(f"fixture missing: {fixture_path}")
        fixture_text = fixture_path.read_text(encoding="utf-8")
        fixture_basename = fixture_path.stem  # "evidence_log_clean"

        # Plan 05-01 — resolve extra fixture names to texts. Names accept
        # both bare basenames ("evidence_log_for_overlap_b.log") and
        # subdirectory-prefixed paths ("evidence/evidence_log_for_overlap_b.log").
        # Mirrors the load_fixture helper's lookup logic.
        extra_evidence_texts: list[str] = []
        for extra_name in extra_evidence_fixtures:
            extra_path = fixtures_dir / extra_name
            if not extra_path.is_file():
                # Fall back to evidence/ subdirectory so callers can pass a
                # bare basename (matches the primary fixture lookup pattern
                # used by test_evidence.py: "evidence/evidence_log_clean.log").
                extra_path = fixtures_dir / "evidence" / extra_name
            if not extra_path.is_file():
                raise FileNotFoundError(
                    f"extra evidence fixture missing: {extra_name}"
                )
            extra_evidence_texts.append(
                extra_path.read_text(encoding="utf-8")
            )

        # Decide whether to use cat-replay rewriting. Some fixtures NEED
        # their original cmd preserved for the test-token to surface:
        #   - timeout fixture: must run ``sleep 999`` to trigger SIGTERM
        #   - volatile_undeclared: cat-replay would byte-match (defeating
        #     the EVIDENCE_OUTPUT_MISMATCH expectation), so we leave the
        #     header but tweak body.txt to differ
        use_cat_replay = True
        body_tweak: str | None = None
        if "timeout" in fixture_basename:
            # Leave cmd as ``sleep 999``; verify_evidence kills it at 5s.
            use_cat_replay = False
        elif "volatile_undeclared" in fixture_basename:
            # cat-replay but write a TWEAKED replay so re-exec emits a
            # subtly different body. Volatile patterns NOT declared, so
            # comparator catches the divergence → EVIDENCE_OUTPUT_MISMATCH.
            # Tweak: replay version replaces "18ms" with "99ms" (committed
            # log retains "18ms"). After byte-match, they differ.
            body_tweak = "MARK_FOR_TWEAK_VOLATILE_UNDECLARED"
        elif "no_cmd" in fixture_basename:
            # Fixture has no ``# evidence-cmd:`` header line; leave it.
            use_cat_replay = False
        elif "volatile_malformed" in fixture_basename:
            # Fixture has invalid regex in volatile header; verify_evidence
            # raises EVIDENCE_VOLATILE_MALFORMED on the byte-match path.
            # Leave cmd as ``pytest`` (which will fail to run) — but in
            # the synth env pytest doesn't exist, so re-exec exits non-zero
            # FIRST. Need cat-replay so we reach the volatile-redaction step.
            use_cat_replay = True
        elif "orphaned_commit" in fixture_basename:
            # The TEST is about commit resolution failure, not the fixture
            # content. cat-replay or not is irrelevant.
            use_cat_replay = True
        elif "stub_first_line" in fixture_basename:
            # cat-replay; the test exercises stub-pattern hit on body.
            use_cat_replay = True
        elif "fabricated_pass" in fixture_basename:
            # 5-byte PASS\n; cat-replay so re-exec emits PASS\n.
            use_cat_replay = True

        # Synth project root.
        project_root = tmp_path / f"project-{casting_id}"
        synth_commit = _build_synth_project(
            project_root,
            casting_id=casting_id,
            fixture_text=fixture_text,
            fixture_basename=fixture_basename,
            spec_format_version=spec_format_version,
            force_exit_code=force_exit_code,
            inject_non_utf8=inject_non_utf8,
            use_cat_replay=use_cat_replay,
            omit_required_evidence=omit_required_evidence,
            # Plan 05-01 — Phase 5 evidence-for kwargs threaded through.
            evidence_for_value=evidence_for_value,
            omit_evidence_for_header=omit_evidence_for_header,
            extra_evidence_fixtures=extra_evidence_fixtures,
            extra_evidence_texts=tuple(extra_evidence_texts),
            casting_req_ids_override=casting_req_ids_override,
        )

        # Apply replay-file tweak post-commit IFF requested. The
        # volatile_undeclared scenario rewrites replay.txt to a subtly-
        # different copy of the committed evidence so byte-match diverges.
        if body_tweak == "MARK_FOR_TWEAK_VOLATILE_UNDECLARED":
            replay_path = project_root / "replay.txt"
            original = replay_path.read_text(encoding="utf-8")
            tweaked = re.sub(r"\b\d+ms\b", "99ms", original)
            # If the regex didn't change anything, force a divergence.
            if tweaked == original:
                tweaked = original + "EXTRA_LINE_THAT_ISNT_IN_COMMITTED\n"
            replay_path.write_text(tweaked, encoding="utf-8")
            _git(["add", "replay.txt"], cwd=project_root)
            _git(["commit", "-q", "-m", "replay tweak"], cwd=project_root)
            head = _git(["rev-parse", "HEAD"], cwd=project_root)
            synth_commit = head.stdout.strip()

        # Resolve the commit to use.
        effective_commit: str
        if casting_commit is not None:
            effective_commit = casting_commit
        elif force_orphaned_commit:
            effective_commit = "0" * 40  # unresolvable
        else:
            effective_commit = synth_commit

        # Run dir for worktree storage.
        run_dir = tmp_path / f"run-{casting_id}"
        run_dir.mkdir(exist_ok=True)

        # Pre-seed orphan worktree if requested.
        orphans_seeded = 0
        pre_count = _count_worktree_metadata_dirs(project_root)
        if seed_orphan_worktree:
            # Reset the prune guard so this test's project_root isn't
            # accidentally skipped by a prior test's pruning.
            from foundry_mcp.tools.evidence import _PRUNE_DONE_FOR

            _PRUNE_DONE_FOR.discard(str(project_root.resolve()))
            orphans_seeded = _seed_orphan_worktree(project_root, run_dir)
            pre_count = _count_worktree_metadata_dirs(project_root)

        # Concurrent invocation path.
        results_holder: list[dict[str, Any]] = []
        errors_holder: list[BaseException] = []

        def _invoke_once(cid: int) -> None:
            try:
                r = verify_evidence(
                    casting_id=cid,
                    project_root=project_root,
                    casting_commit=effective_commit,
                    spec_path=project_root / "specs" / "spec.md",
                    run_dir=run_dir,
                )
                results_holder.append(r)
            except BaseException as exc:  # noqa: BLE001
                errors_holder.append(exc)

        if concurrent_invocations > 1:
            # Each concurrent thread must verify a DIFFERENT casting_id so
            # the per-thread worktree paths don't collide (verify_evidence
            # creates ``run_dir/worktrees/casting-{id}/`` — same id from
            # two threads would race on the same path).
            #
            # We commit additional evidence files (one per extra
            # casting_id) into the same project_root before spawning the
            # threads. The threading.Lock inside ``_setup_worktree``
            # serializes the ``git worktree add`` calls (Pitfall 2);
            # ``.git/config.lock`` contention is the property under test.
            extra_ids = list(range(casting_id, casting_id + concurrent_invocations))
            for extra_id in extra_ids[1:]:
                # Copy the rewritten evidence + replay for each extra id.
                src_log = (
                    project_root
                    / "evidence"
                    / f"casting-{casting_id}-{fixture_basename}.log"
                )
                dst_log = (
                    project_root
                    / "evidence"
                    / f"casting-{extra_id}-{fixture_basename}.log"
                )
                dst_log.write_text(src_log.read_text(encoding="utf-8"))
            # Re-commit so the new evidence files are inside the casting commit.
            _git(["add", "."], cwd=project_root)
            _git(
                ["commit", "-q", "-m", "extra concurrent castings"],
                cwd=project_root,
            )
            head = _git(["rev-parse", "HEAD"], cwd=project_root)
            effective_commit = head.stdout.strip()

            threads = [
                threading.Thread(target=_invoke_once, args=(eid,))
                for eid in extra_ids
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=120)
            if errors_holder:
                raise errors_holder[0]
            primary = results_holder[0]
        else:
            _invoke_once(casting_id)
            if errors_holder:
                raise errors_holder[0]
            primary = results_holder[0]

        # Post-call: count orphan-prune delta.
        post_count = _count_worktree_metadata_dirs(project_root)
        orphans_pruned = max(0, pre_count - post_count) if seed_orphan_worktree else 0

        # Worktree teardown verification: the verify_evidence try/finally
        # always tears down; harness exposes this via a manifest field.
        worktree_path = run_dir / "worktrees" / f"casting-{casting_id}"
        worktree_torn_down = not worktree_path.exists()

        # Plan 04-04 — surface stream_skips + f09_diagnostics + castings
        # array from the production manifest.json (verify_evidence writes the
        # v2.0 stream-skip record + provenance records there).
        manifest_path = project_root / "castings" / "manifest.json"
        persisted_stream_skips: list[dict[str, Any]] = []
        persisted_castings: list[dict[str, Any]] = []
        if manifest_path.is_file():
            try:
                persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
                persisted_stream_skips = persisted.get("stream_skips", []) or []
                persisted_castings = persisted.get("castings", []) or []
            except json.JSONDecodeError:
                pass

        # Plan 04-04 / F0.9 sub-check 7k: when v2.1 spec lacks an evidence
        # record where one was required (omit_required_evidence=True), the
        # F0.9 7k re-derivation logic flags it as STREAM_SKIP_INCOMPLETE
        # naming EVID-01. The harness synthesizes the diagnostic string here
        # since F0.9 itself runs at the lead-orchestrator level (start.md
        # F0.9 prose), not inside verify_evidence — but the underlying
        # signal (no provenance record on a v2.1+ spec where one was
        # required) is what verify_evidence surfaces via verdict='rejected'
        # + EVIDENCE_COMMAND_MISSING.
        f09_diagnostics_parts: list[str] = []
        if omit_required_evidence and spec_format_version == "v2.1":
            # No evidence file committed → verify_evidence rejects with
            # EVIDENCE_COMMAND_MISSING. F0.9 7k machinery would re-derive
            # the expected EVID-01 record from F0.5 step 2b roster and
            # detect its absence from manifest.stream_skips (since EVID-01
            # is in the v2.1+ engaged set, not stream-skipped). Mirrors
            # Phase 3's "absence of stream-skipped record on legacy spec
            # is itself a defect" pattern, inverted: absence of a
            # provenance record on v2.1+ when one was required is a defect.
            f09_diagnostics_parts.append(
                "STREAM_SKIP_INCOMPLETE: EVID-01 — evidence verification "
                "engaged (spec_format_version=v2.1) but no provenance "
                "record produced (no evidence files in casting commit)."
            )

        # Synthesize a manifest dict with fields the tests probe.
        manifest: dict[str, Any] = {
            "worktree_torn_down": worktree_torn_down,
            "orphan_worktrees_pruned": orphans_pruned,
            "concurrent_serialized": concurrent_invocations > 1,
            # Plan 04-04 — populate from persisted manifest.json (production
            # verify_evidence appends v2.0 EVID-01 stream-skip records).
            "stream_skips": persisted_stream_skips,
            "castings": persisted_castings,
            "failures": (
                [
                    {
                        "token": primary.get("failure_token"),
                        "detail": primary.get("failure_detail") or "",
                    }
                ]
                if primary.get("failure_token")
                else []
            ),
            "f09_diagnostics": "\n".join(f09_diagnostics_parts),
        }

        provenance = (
            primary["provenance_records"][0]
            if primary.get("provenance_records")
            else None
        )

        # ============================================================
        # Plan 05-03 / EVID-02 — integration dispatch through
        # foundry_accept_casting.
        #
        # The Phase 5 per-requirement-coverage gate lives in
        # foundry_accept_casting (foundry_handoff.py); verify_evidence
        # alone cannot surface EVIDENCE_REQUIREMENT_UNBOUND. When the
        # caller passes ``casting_req_ids_override``, the harness sets up
        # an active foundry run, wires the spec + prompt into the run
        # dir at the filenames foundry_accept_casting expects (with-dash
        # ``casting-{id}-prompt.md``), and dispatches through it. The
        # Phase 5 result fields (``failure_token``, ``unbound_requirements``,
        # ``ok``) override the verify_evidence-derived defaults.
        #
        # Phase 4 callsites (no override) skip this branch entirely so
        # the Phase 4 24-test surface continues to run on the verify_evidence-
        # only path.
        # ============================================================
        if casting_req_ids_override is not None:
            from foundry_mcp.tools.foundry_handoff import (
                foundry_accept_casting as _accept,
                _hash_str as _h,
            )
            from foundry_mcp.tools.foundry_state import (
                set_active_run as _set_run,
                clear_active_run as _clear_run,
                ARCHIVE_DIR as _ARCH,
            )

            run_name = f"phase5-test-{casting_id}-{os.getpid()}"
            fdir = project_root / _ARCH / run_name
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / "castings").mkdir(parents=True, exist_ok=True)

            # Copy spec.md to fdir (foundry_accept_casting reads
            # ``fdir / 'spec.md'`` via foundry_spec_hash).
            spec_src = project_root / "specs" / "spec.md"
            spec_dst = fdir / "spec.md"
            spec_text = spec_src.read_text(encoding="utf-8")
            spec_dst.write_text(spec_text, encoding="utf-8")
            spec_hash = _h(spec_text)

            # Copy the casting prompt to fdir under the dash-form filename
            # foundry_accept_casting reads (``casting-{id}-prompt.md``).
            prompt_src = (
                project_root / "castings" / f"casting-{casting_id}.prompt.md"
            )
            prompt_dst = fdir / "castings" / f"casting-{casting_id}-prompt.md"
            prompt_text = prompt_src.read_text(encoding="utf-8")
            prompt_dst.write_text(prompt_text, encoding="utf-8")
            prompt_hash = _h(prompt_text)

            # Synthesize a completion report that satisfies citation
            # discipline for each req_id (file:line within 300 chars of
            # each ID mention) so missing_citations stays empty and
            # doesn't mask the EVIDENCE_REQUIREMENT_UNBOUND signal.
            completion_lines = [
                f"{rid} implemented at src/synth.py:{42 + idx}"
                for idx, rid in enumerate(casting_req_ids_override)
            ]
            completion_report = "\n".join(completion_lines) + "\n"

            _set_run(run_name)
            try:
                accept_result = _accept(
                    casting_id=casting_id,
                    spec_hash=spec_hash,
                    prompt_hash=prompt_hash,
                    completion_report=completion_report,
                    project_root=str(project_root),
                    casting_commit=effective_commit,
                )
            finally:
                _clear_run()

            # Merge accept_result fields into the harness return shape.
            # Verify_evidence's verdict/provenance is preserved when
            # foundry_accept_casting accepted; on rejection-path we surface
            # accept_result's failure_token + unbound_requirements.
            #
            # Verdict mapping:
            #   verify_evidence "skipped"  → keep "skipped" (v2.0 path —
            #     accept_result also reports evidence_verdict='skipped'
            #     and the Phase 5 gate is bypassed by guard).
            #   accept_result["ok"] False  → "rejected" (Phase 5 unbound
            #     check fired, OR Phase 4 hard-reject already fired).
            #   accept_result["ok"] True   → "accepted" (v2.1 full
            #     coverage OR v2.0 stream-skip).
            verdict_from_accept: str
            if primary["verdict"] == "skipped":
                verdict_from_accept = "skipped"
            elif accept_result.get("ok") is False:
                verdict_from_accept = "rejected"
            else:
                verdict_from_accept = "accepted"

            return {
                "verdict": verdict_from_accept,
                "failure_token": accept_result.get("failure_token")
                or primary.get("failure_token"),
                "failure_detail": accept_result.get("failure_detail")
                or primary.get("failure_detail"),
                "unbound_requirements": accept_result.get("unbound_requirements"),
                "provenance": provenance,
                "all_provenance": primary.get("provenance_records", []),
                "manifest": manifest,
                "manifest_updates": primary.get("manifest_updates", {}),
                "accept_result": accept_result,
            }

        return {
            "verdict": primary["verdict"],
            "failure_token": primary.get("failure_token"),
            "failure_detail": primary.get("failure_detail"),
            "provenance": provenance,
            "all_provenance": primary.get("provenance_records", []),
            "manifest": manifest,
            "manifest_updates": primary.get("manifest_updates", {}),
        }

    return _run


# ---------------------------------------------------------------------------
# Phase 7 / TEST-01 fixtures
#
# Two append-only fixtures driving the Phase 7 spec-derived test stream tests
# in tests/test_spec_test_deriver.py. Mirrors Phase 6 plugins/forge/tests/
# test_spec_review.py's _run_validator helper shape and Phase 4
# run_accept_casting_with_evidence's RED-or-SKIP discipline (signature
# locked here in Plan 07-01; fixture body wires up automatically once
# Plan 07-02 ships the validator script).
# ---------------------------------------------------------------------------

VALIDATE_TEST_OBSERVATIONS_PATH = (
    # tests/conftest.py -> parents: [0]=tests, [1]=mcp-server, [2]=foundry,
    # [3]=plugins, [4]=repo-root.
    Path(__file__).resolve().parents[4]
    / "plugins" / "foundry" / "scripts" / "validate-test-observations.py"
)


@pytest.fixture
def run_test_observations_validator(
    tmp_path: Path,
) -> Callable[..., tuple[int, str, str]]:
    """Invoke ``validate-test-observations.py`` via subprocess.

    Plan 07-01 ships the SKIP stub: until ``validate-test-observations.py``
    exists on disk (Plan 07-02 territory), tests requesting this fixture
    SKIP cleanly. Once the validator script lands, the runner kicks in
    with no edits to test_spec_test_deriver.py — mirrors Plan 03-01's
    ``run_f05_decompose_with_test_roster`` precedent (signature locked
    in Wave-0; body activates when downstream plan ships).

    Mirrors Phase 6 plugins/forge/tests/test_spec_review.py:_run_validator
    shape: subprocess.run with capture_output=True + text=True + timeout=30 +
    check=False; returns ``(exit_code, stdout, stderr)``.
    """
    if not VALIDATE_TEST_OBSERVATIONS_PATH.exists():
        pytest.skip(
            "validate-test-observations.py not yet shipped — "
            "Plan 07-02 territory",
        )

    def _runner(
        observation_path: Path,
        *,
        spec_path: Path | None = None,
        tool_call_log_path: Path | None = None,
    ) -> tuple[int, str, str]:
        argv: list[str] = [
            "python3",
            str(VALIDATE_TEST_OBSERVATIONS_PATH),
            str(observation_path),
        ]
        if spec_path is not None:
            argv.extend(["--spec", str(spec_path)])
        if tool_call_log_path is not None:
            argv.extend(["--tool-call-log", str(tool_call_log_path)])
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr

    return _runner


@pytest.fixture
def mock_uvx_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """Intercept ``subprocess.run`` for ``uvx`` invocations.

    Plan 07-03 territory tests assert that the spec-test-deriver agent
    wrapper calls ``uvx --from hypothesis-jsonschema --with hypothesis
    python -m pytest ...`` shape correctly. This fixture monkeypatches
    ``subprocess.run`` so any invocation whose first cmd-token contains
    ``"uvx"`` is intercepted: a synthetic empty-observations JSON is
    returned and the cmd is recorded under ``recorded["calls"]``.
    Non-uvx subprocess.run calls pass through to the real implementation
    unchanged (so git, python imports, etc. still work).

    Returns a dict with ``calls`` key (list of recorded cmd lists).
    """
    recorded: dict[str, Any] = {"calls": []}
    _real_run = subprocess.run

    def _fake_run(
        *args: Any, **kwargs: Any
    ) -> subprocess.CompletedProcess:
        cmd = args[0] if args else kwargs.get("args", [])
        recorded["calls"].append(cmd)
        if isinstance(cmd, list) and cmd and "uvx" in str(cmd[0]):
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout=json.dumps(
                    {
                        "stream": "TEST-01",
                        "cycle": 1,
                        "spec_format_version": "v2.1",
                        "spec_hash": "sha256:stub",
                        "agent_path": (
                            "plugins/foundry/agents/spec-test-deriver.md"
                        ),
                        "wall_clock_seconds": 0.0,
                        "uvx_subprocess_seconds": 0.0,
                        "observations": [],
                    }
                ),
                stderr="",
            )
        return _real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return recorded


# ---------------------------------------------------------------------------
# Phase 8 / INTENT-01 fixtures
#
# Append-only extension mirroring Phase 7's run_test_observations_validator
# shape verbatim. The validator script (validate-intent-coverage.py) lands
# in Plan 08-02; until then, the fixture pytest.skip()s the calling test
# at fixture-acquire time. Module-top guard inside test_intent_coverage.py
# ALSO skips at module level when the script is missing — defense-in-depth.
# ---------------------------------------------------------------------------

VALIDATE_INTENT_COVERAGE_PATH = (
    # tests/conftest.py -> parents: [0]=tests, [1]=mcp-server, [2]=foundry,
    # [3]=plugins, [4]=repo-root. Mirrors VALIDATE_TEST_OBSERVATIONS_PATH
    # depth — Phase 7 STATE.md noted parents[3] vs parents[4] confusion;
    # parents[4] is the verified-working form for this conftest layout.
    Path(__file__).resolve().parents[4]
    / "plugins" / "foundry" / "scripts" / "validate-intent-coverage.py"
)


@pytest.fixture
def run_intent_coverage_validator(
    tmp_path: Path,
) -> Callable[..., tuple[int, str, str]]:
    """Invoke ``validate-intent-coverage.py`` via subprocess.

    Plan 08-01 ships the SKIP stub: until ``validate-intent-coverage.py``
    exists on disk (Plan 08-02 territory), tests requesting this fixture
    SKIP cleanly. Once the validator script lands, the runner kicks in
    with no edits to test_intent_coverage.py — mirrors Plan 07-01's
    ``run_test_observations_validator`` precedent (signature locked
    in Wave-0; body activates when downstream plan ships).

    Mirrors Phase 7 ``run_test_observations_validator`` shape:
    subprocess.run with capture_output=True + text=True + timeout=30 +
    check=False; returns ``(exit_code, stdout, stderr)``.
    """
    if not VALIDATE_INTENT_COVERAGE_PATH.exists():
        pytest.skip(
            "validate-intent-coverage.py not yet shipped — "
            "Plan 08-02 territory",
        )

    def _runner(
        coverage_path: Path,
        *,
        spec_path: Path | None = None,
        tool_call_log_path: Path | None = None,
    ) -> tuple[int, str, str]:
        argv: list[str] = [
            "python3",
            str(VALIDATE_INTENT_COVERAGE_PATH),
            str(coverage_path),
        ]
        if spec_path is not None:
            argv.extend(["--spec", str(spec_path)])
        if tool_call_log_path is not None:
            argv.extend(["--tool-call-log", str(tool_call_log_path)])
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.returncode, result.stdout, result.stderr

    return _runner
