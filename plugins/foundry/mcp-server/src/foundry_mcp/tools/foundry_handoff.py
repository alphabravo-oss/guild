"""Foundry handoff audit log.

Every handoff event in a Foundry run must be recorded through
`Foundry-Handoff`. This creates an inspectable trail showing which
artifacts were produced from which sources, with integrity hashes,
and whether the lead re-read the source before the handoff happened.

Handoff events:
  - spec_to_casting:     spec.md → castings/manifest.json + casting-N-prompt.md
  - casting_to_teammate: casting-N-prompt.md → Agent spawn
  - teammate_to_accepted: teammate completion report → lead acceptance
  - inspect_to_grind:    defects → grind tasks
  - grind_to_inspect:    grind fixes → re-verification
  - assay_to_done:       ASSAY verdicts → F6 DONE
  - spec_to_decompose:   (re-read) lead re-reads spec before decomposing
  - any other transition the lead wants audited

The log is JSONL at `foundry-archive/{run}/handoffs.jsonl` (machine
readable) and mirrored to `handoffs.md` (human readable).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.tools.citation import CITATION_PATTERN, unresolved_symbol_cites
from foundry_mcp.tools.foundry_state import get_run_dir


def _hash_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"sha256:{h[:16]}"


def _hash_str(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def foundry_handoff(
    event: str,
    source: str = "",
    destination: str = "",
    source_reread: bool = False,
    summary: str = "",
    information_loss: str = "",
    project_root: str = ".",
) -> dict:
    """Record a handoff event.

    Args:
        event: One of spec_to_casting, casting_to_teammate, teammate_to_accepted,
            inspect_to_grind, grind_to_inspect, assay_to_done, spec_reread,
            or a custom short name.
        source: Path to the source artifact (relative to project root). If the
            path exists, its hash is recorded automatically.
        destination: Path to the destination artifact.
        source_reread: The lead MUST set this to True if the handoff involves
            re-reading the source (e.g. spec → casting, spec → teammate prompt,
            spec → acceptance). False means "the lead acted from prior memory,"
            which is logged but flagged.
        summary: One-line description of what this handoff accomplished.
        information_loss: If the destination artifact contains less of the
            spec than the source, describe what was dropped. Non-empty value
            is a warning flag (prompts the lead to justify).

    Returns:
        {
            "ok": True,
            "event": ...,
            "handoff_id": "uuid",
            "source_hash": ...,
            "destination_hash": ...,
            "source_reread": bool,
            "warning": str | None
        }
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run"}
    if not fdir.exists():
        fdir.mkdir(parents=True, exist_ok=True)

    root = Path(project_root).resolve()
    source_path = (root / source) if source and not Path(source).is_absolute() else Path(source) if source else None
    dest_path = (root / destination) if destination and not Path(destination).is_absolute() else Path(destination) if destination else None

    source_hash = _hash_file(source_path) if source_path else None
    dest_hash = _hash_file(dest_path) if dest_path else None

    timestamp = datetime.now(timezone.utc).isoformat()
    handoff_id = _hash_str(f"{timestamp}|{event}|{source}|{destination}")

    entry = {
        "handoff_id": handoff_id,
        "timestamp": timestamp,
        "event": event,
        "source": source,
        "source_hash": source_hash,
        "destination": destination,
        "destination_hash": dest_hash,
        "source_reread": bool(source_reread),
        "summary": summary,
        "information_loss": information_loss,
    }

    warning = None
    if information_loss:
        warning = f"Information loss reported: {information_loss}. Lead must justify or re-decompose."
    if not source_reread and event in {"spec_to_casting", "spec_reread", "spec_to_decompose", "acceptance"}:
        warning = (warning + "; " if warning else "") + (
            f"source_reread=False for event '{event}'. Lead acted from memory, "
            f"not a fresh read of the source. Context rot risk."
        )

    # Write JSONL
    jsonl_path = fdir / "handoffs.jsonl"
    with jsonl_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    # Mirror to human-readable markdown
    md_path = fdir / "handoffs.md"
    header_needed = not md_path.exists()
    with md_path.open("a", encoding="utf-8") as f:
        if header_needed:
            f.write("# Foundry Handoff Audit Log\n\n")
            f.write("Every transition between phases or artifacts is recorded here.\n\n")
        f.write(f"## {event} — {timestamp}\n")
        f.write(f"- handoff_id: `{handoff_id}`\n")
        if source:
            f.write(f"- source: `{source}` ({source_hash or 'no file'})\n")
        if destination:
            f.write(f"- destination: `{destination}` ({dest_hash or 'no file'})\n")
        f.write(f"- source_reread: `{source_reread}`\n")
        if summary:
            f.write(f"- summary: {summary}\n")
        if information_loss:
            f.write(f"- **information_loss**: {information_loss}\n")
        if warning:
            f.write(f"- **WARNING**: {warning}\n")
        f.write("\n")

    return {
        "ok": True,
        "handoff_id": handoff_id,
        "event": event,
        "source_hash": source_hash,
        "destination_hash": dest_hash,
        "source_reread": source_reread,
        "warning": warning,
        "log_entry": entry,
    }


def foundry_spec_hash(project_root: str = ".") -> dict:
    """Return the current sha256 of spec.md. Lead calls this to obtain a
    hash that must be passed to `Foundry-Spawn-Teammate` and
    `Foundry-Accept-Casting`. The tools verify the hash matches the
    current file content, forcing the lead to actually Read the spec
    rather than relying on prior context.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run"}

    spec_path = fdir / "spec.md"
    if not spec_path.exists():
        state_path = fdir / "state.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            sp = state.get("spec_path", "")
            if sp:
                candidate = Path(project_root) / sp
                if candidate.exists():
                    spec_path = candidate

    if not spec_path.exists():
        return {"ok": False, "error": "spec.md not found in run directory or state"}

    h = _hash_file(spec_path)
    size = spec_path.stat().st_size
    mtime = datetime.fromtimestamp(spec_path.stat().st_mtime, tz=timezone.utc).isoformat()

    return {
        "ok": True,
        "spec_path": str(spec_path),
        "spec_hash": h,
        "size_bytes": size,
        "mtime": mtime,
        "instruction": (
            "Read the spec.md file now. Then pass the spec_hash to every "
            "Foundry-Spawn-Teammate and Foundry-Accept-Casting call. If you "
            "do not re-Read the spec first, you are acting from memory — "
            "this violates the context-rot prevention rule."
        ),
    }


def foundry_accept_casting(
    casting_id: int | str,
    spec_hash: str,
    prompt_hash: str,
    completion_report: str,
    project_root: str = ".",
    *,
    casting_commit: str | None = None,
) -> dict:
    """Gate the acceptance of a completed casting.

    The lead MUST call this before marking any casting done. The tool:
      1. Verifies spec_hash matches the current spec.md (forces re-read)
      2. Verifies prompt_hash matches the casting's prompt file (forces
         the lead to have read the authoritative prompt, not a memory)
      3. Records the acceptance as a handoff entry
      4. Returns the list of acceptance criteria from the casting's
         <spec_requirements> block so the lead can verify each against
         the completion report
      5. **Phase 4 / EVID-01:** Re-runs the teammate's cited evidence
         commands server-side via ``verify_evidence``. On v2.0 specs,
         records an EVID-01 stream-skip (Phase 1/2/3 backwards-compat).
         On v2.1+ specs, re-executes each ``evidence/casting-{id}-*.log``
         in an isolated worktree and rejects on byte-mismatch, timeout,
         non-zero exit, missing command, malformed volatile regex, or
         stub-pattern hit.

    It does NOT mechanically check that the completion report satisfies
    the ACs — that requires semantic understanding. It provides the
    authoritative AC list and forces the lead to acknowledge it.

    Args:
        casting_id: Casting id from manifest.json
        spec_hash: Fresh sha256 of spec.md (from Foundry-Spec-Hash)
        prompt_hash: Hash of casting-{id}-prompt.md (from Foundry-Spawn-Teammate)
        completion_report: The teammate's completion report text
        project_root: Repo root
        casting_commit: Phase 4 / EVID-01 — full SHA of the casting's
            commit (rev-parseable). Required for evidence re-execution
            (``verify_evidence`` checks out this commit in a detached
            worktree). When None, evidence verification is bypassed
            (Phase 4 backwards-compat for callers not yet updated).

    Returns:
        On success:
            {"ok": True, "casting_id": N, "acceptance_criteria": [...],
             "must_verify": [...], "warning": str | None,
             "unresolved_symbol_cites": [],
             "evidence_verdict": "accepted" | "skipped",
             "evidence_provenance": [...],
             "evidence_spec_path": str | None}
        On failure:
            {"ok": False, "error": "...", "hint": "..."}
        On evidence rejection:
            {"ok": False, "failure_token": "EVIDENCE_*", "failure_detail": "...",
             "evidence_provenance": [...]}

    ``evidence_spec_path`` names the spec the evidence run actually read —
    resolved from the RUN (``foundry_spec_hash``), never re-derived from a
    fixed ``<project_root>/specs/spec.md`` guess. It is the observable proof
    that a v2.1 run engaged evidence re-execution rather than silently routing
    through the v2.0 stream-skip branch.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"ok": False, "error": "No active foundry run"}

    # Verify spec hash
    spec_result = foundry_spec_hash(project_root=project_root)
    if not spec_result.get("ok"):
        return {"ok": False, "error": f"Cannot hash spec: {spec_result.get('error')}"}
    current_spec_hash = spec_result["spec_hash"]
    if spec_hash != current_spec_hash:
        return {
            "ok": False,
            "error": "stale_spec_hash",
            "hint": (
                f"Spec hash mismatch. You passed {spec_hash!r} but current is "
                f"{current_spec_hash!r}. Re-read spec.md and try again with the "
                f"fresh hash. Never accept a casting using a spec hash from "
                f"memory — the spec may have been updated mid-run."
            ),
        }

    # Load the casting prompt
    prompt_path = fdir / "castings" / f"casting-{casting_id}-prompt.md"
    if not prompt_path.exists():
        return {
            "ok": False,
            "error": f"casting-{casting_id}-prompt.md not found",
            "hint": "Re-run F0.5 DECOMPOSE",
        }

    prompt_text = prompt_path.read_text(encoding="utf-8")
    current_prompt_hash = _hash_str(prompt_text)
    if prompt_hash != current_prompt_hash:
        return {
            "ok": False,
            "error": "stale_prompt_hash",
            "hint": (
                f"Casting prompt hash mismatch. Call Foundry-Spawn-Teammate "
                f"first to get a fresh prompt hash, then retry acceptance."
            ),
        }

    # Extract acceptance criteria from the <spec_requirements> block
    import re
    match = re.search(
        r"<spec_requirements>(.*?)</spec_requirements>",
        prompt_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return {
            "ok": False,
            "error": "casting prompt has no <spec_requirements> block",
            "hint": "F0.9 VALIDATE should have caught this. Re-run validation.",
        }

    spec_block = match.group(1).strip()
    acs = [ln.strip() for ln in spec_block.splitlines() if ln.strip()]

    # Requirement-ID citation check.
    #
    # Parse every tagged requirement ID from the casting's <spec_requirements>
    # block. For each ID, verify the completion report contains a citation
    # within 300 chars of the ID mention. Missing citations mean the teammate
    # did not (or cannot) prove that requirement was implemented — mechanical
    # proof-of-coverage, prevents drift between what the spec asked for and
    # what the teammate claims was built.
    #
    # FR-004 / AC-005: BOTH cite forms count — the durable `path#Symbol` form
    # and the legacy `file:line` form. The grammar lives in
    # `citation.CITATION_PATTERN` so the gate and the resolution guard below
    # cannot drift apart; widening it here never narrows what was accepted
    # before, because the file:line alternative is carried through unchanged.
    # THE WINDOW IS SYMMETRIC (D-118). It used to be
    # `completion_report[start:start + 300]` — forward from the ID only — while
    # agents/teammate.md tells every teammate the gate "verifies each
    # requirement ID has a citation WITHIN 300 CHARACTERS OF the ID mention".
    # So `src/mod.py#foo implements FR-001` satisfies the documented
    # instruction and was rejected, costing a re-dispatch bounce on a report
    # that was already correct. The failure was safe (over-strict, never
    # over-permissive), which is why it survived — a gate that only ever
    # refuses too much produces no bad acceptances to notice, just wasted
    # cycles. Widening the code to match the prose is the fix; the prose is
    # another casting's file and needs no change.
    CITATION_WINDOW = 300
    req_id_pattern = r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b"
    casting_req_ids = sorted(set(re.findall(req_id_pattern, spec_block)))
    citation_pattern = CITATION_PATTERN
    missing_citations: list = []
    for rid in casting_req_ids:
        # Find every occurrence of the requirement ID in the report.
        found_citation = False
        for m in re.finditer(re.escape(rid), completion_report):
            start = m.start()
            window = completion_report[
                max(0, start - CITATION_WINDOW):m.end() + CITATION_WINDOW
            ]
            if citation_pattern.search(window):
                found_citation = True
                break
        if not found_citation:
            missing_citations.append(rid)

    # Mechanical symbol-resolution guard (FR-004 / AC-006 / AC-007).
    #
    # A new rung on the precondition ladder: every `path#Symbol` cite in the
    # report must resolve in the tree. A cite whose symbol resolves is VALID
    # however stale a `:line` hint beside it has become — the guard never
    # reads the line component, so a moved line produces no finding of any
    # kind. A symbol that resolves nowhere is a defect and blocks acceptance.
    unresolved_cites = unresolved_symbol_cites(completion_report, project_root)

    # ============================================================
    # Phase 4 / EVID-01: server-side evidence re-execution.
    #
    # Inserted between the req-ID citation check and the scope-flag check.
    # On v2.0 specs, verify_evidence routes through manifest.stream_skips
    # (Phase 1/2/3 backwards-compat — same machinery as Phase 3 stream-skips
    # but with EVID-01 as a virtual stream owned by foundry_accept_casting).
    # On v2.1+ specs, every cited evidence command is re-run server-side
    # and rejected on byte-mismatch, timeout, non-zero exit, missing
    # command, malformed volatile regex, or stub-pattern hit.
    #
    # casting_commit=None is the backwards-compat shim for callers not yet
    # updated; evidence verification is bypassed in that case so the gate
    # doesn't break test fixtures + prior-Phase callsites that haven't
    # migrated. Production callers MUST pass the SHA from the teammate's
    # completion report (via Foundry-Spawn-Teammate or git rev-parse HEAD).
    # ============================================================
    evidence_verdict = None
    evidence_provenance: list[dict] = []
    evidence_spec_path: Path | None = None
    evidence_stream_skips: list[dict] = []
    if casting_commit is not None:
        from foundry_mcp.tools.evidence import (
            _declared_spec_format_version,
            _read_spec_format_version,
            verify_evidence,
        )
        from foundry_mcp.tools.foundry_state import get_run_dir as _get_run_dir

        # Resolve run_dir for worktree storage. fdir is the active foundry
        # run dir (computed at function entry); pass it through so the
        # worktree lives under foundry-archive/{run}/worktrees/.
        #
        # FR-017 / AC-023: the spec path is the RUN's spec, not a third
        # re-derivation. `foundry_spec_hash` already resolved it above
        # (fdir/spec.md, else the spec_path recorded in state.json) and the
        # result is in `spec_result`. The hardcoded
        # `<project_root>/specs/spec.md` this replaces pointed at a file most
        # runs do not have; `verify_evidence` reads `spec_format_version` off
        # whatever path it is handed and defaults to v2.0 on a miss, so the
        # wrong path silently downgraded every v2.1 run to the stream-skip
        # branch and no evidence was ever re-executed.
        evidence_spec_path = Path(spec_result["spec_path"])

        # A DECLARED but unparseable spec_format_version is a precondition
        # failure, and it is refused HERE, in the same {ok, error, hint} shape
        # as every other rung of this ladder. It is not an evidence failure —
        # nothing was re-executed and no evidence file is at fault — so it
        # carries no KNOWN_EVIDENCE_FAILURE_TOKENS name. What it must not do is
        # what it used to: parse as v2.0, skip re-execution, and return
        # `ok: true`. A typo in one frontmatter line bought a green gate.
        if _read_spec_format_version(evidence_spec_path) is None:
            declared = _declared_spec_format_version(evidence_spec_path)
            return {
                "ok": False,
                "casting_id": casting_id,
                "error": "malformed_spec_format_version",
                "evidence_spec_path": str(evidence_spec_path),
                "declared_spec_format_version": declared,
                "hint": (
                    f"{evidence_spec_path} declares spec_format_version "
                    f"{declared!r}, which is not a vN.N version. Evidence "
                    f"verification will not guess a version and will not "
                    f"silently downgrade the run to v2.0. Fix the spec's "
                    f"frontmatter to a real version (e.g. `spec_format_version: "
                    f"v2.1`) and re-run acceptance."
                ),
            }

        evidence_result = verify_evidence(
            casting_id=casting_id,
            project_root=Path(project_root),
            casting_commit=casting_commit,
            spec_path=evidence_spec_path,
            run_dir=fdir,
        )
        evidence_verdict = evidence_result["verdict"]
        evidence_provenance = list(evidence_result.get("provenance_records", []))
        # A v2.0 stream-skip means evidence verification was structurally
        # bypassed for this casting. It is persisted in the run's manifest, but
        # the lead reads THIS return — surfacing the record here is what makes
        # the bypass visible at the moment it happens rather than only to
        # whoever later opens the manifest.
        evidence_stream_skips = list(
            evidence_result.get("manifest_updates", {}).get("stream_skips", [])
        )

        # Audit-log per evidence file (two-channel audit: manifest +
        # handoffs.jsonl). Mirrors Phase 1/2/3 dual-channel pattern.
        for record in evidence_provenance:
            foundry_handoff(
                event="evidence_verified",
                source=f"castings/casting-{casting_id}-prompt.md",
                destination=record.get("evidence_path", ""),
                source_reread=True,
                summary=(
                    f"casting {casting_id} evidence verdict={record.get('verdict')} "
                    f"token={record.get('failure_token') or 'none'} "
                    f"elapsed={record.get('elapsed_seconds')}s"
                ),
                information_loss=record.get("failure_detail") or "",
                project_root=project_root,
            )

        # Stdout summary line (Phase 3 F0.5 stdout-summary precedent).
        accepted = sum(
            1 for r in evidence_provenance if r.get("verdict") == "accepted"
        )
        rejected = sum(
            1 for r in evidence_provenance if r.get("verdict") == "rejected"
        )
        tokens = sorted(
            {
                r.get("failure_token")
                for r in evidence_provenance
                if r.get("failure_token")
            }
        )
        print(
            f"Foundry-Accept-Casting: casting {casting_id} — "
            f"evidence verdicts: {accepted} accepted, {rejected} rejected "
            f"(tokens: {','.join(tokens) if tokens else 'none'})",
            flush=True,
        )

        # Hard-reject on evidence verdict='rejected'. Skip path (v2.0)
        # falls through to scope-flag check; the manifest.stream_skips
        # record is the audit signal that evidence verification was
        # structurally bypassed for this run.
        if evidence_verdict == "rejected":
            return {
                "ok": False,
                "casting_id": casting_id,
                "failure_token": evidence_result["failure_token"],
                "failure_detail": evidence_result["failure_detail"],
                "evidence_provenance": evidence_provenance,
                "evidence_spec_path": str(evidence_spec_path),
                "hint": (
                    "Evidence re-execution rejected the casting. The teammate's "
                    "committed log diverges from a clean re-execution of "
                    "`# evidence-cmd:`. Re-run the command yourself, inspect "
                    "the diff, and re-dispatch with corrected evidence."
                ),
            }

        # ============================================================
        # Phase 5 / EVID-02: per-requirement-coverage check (strictness
        # upgrade to EVID-01).
        #
        # Runs only when:
        #   1. evidence verification engaged AND verdict was "accepted"
        #      (skipped → v2.0 stream-skip routing; rejected → Phase 4
        #      already returned; both bypass this check)
        #   2. casting_req_ids is non-empty (zero-req castings — refactors,
        #      doc edits — legitimately need no per-requirement binding)
        #
        # Computes the set difference of casting requirement IDs against
        # the union of `evidence_for` lists across all provenance records.
        # Non-empty difference → reject with named missing IDs.
        #
        # casting_commit=None bypasses the entire enclosing block (Phase 4
        # backwards-compat shim — Pitfall 7); this check inherits.
        #
        # Why set(casting_req_ids) - bound_ids (not the reverse): "unbound"
        # = "in the casting but not bound by any artifact". The reverse
        # direction would surface "over-coverage" (artifact cites IDs not
        # in the casting), which 05-RESEARCH.md decided to silently drop
        # (closed-vocabulary minimization). Over-coverage is not an error.
        # ============================================================
        if (
            evidence_verdict == "accepted"  # don't double-reject after Phase 4 fail
            and casting_req_ids  # zero-req castings need no per-req binding
        ):
            bound_ids: set[str] = set()
            for record in evidence_provenance:
                for rid in record.get("evidence_for", []):
                    bound_ids.add(rid)
            unbound = sorted(set(casting_req_ids) - bound_ids)
            if unbound:
                # Hard-reject with named missing IDs (SC#4 satisfied).
                return {
                    "ok": False,
                    "casting_id": casting_id,
                    "failure_token": "EVIDENCE_REQUIREMENT_UNBOUND",
                    "failure_detail": (
                        f"casting {casting_id} has no evidence artifact bound to "
                        f"requirement(s): {', '.join(unbound)}. Each committed "
                        f"evidence file must carry a `# evidence-for: <ids>` "
                        f"header listing the requirement IDs it demonstrates."
                    ),
                    "unbound_requirements": unbound,
                    "evidence_verdict": evidence_verdict,
                    "evidence_provenance": evidence_provenance,
                    "requirement_ids": casting_req_ids,
                    "hint": (
                        f"Add a `# evidence-for: {', '.join(unbound)}` header "
                        f"line to the relevant evidence file(s) and re-commit. "
                        f"Multiple files may bind to the same requirement; one "
                        f"file may bind to multiple requirements (comma-separated "
                        f"list). See plugins/foundry/agents/teammate.md Step 11 "
                        f"for the canonical evidence-file format."
                    ),
                }

    # Check for "out of scope" or "cut scope" mentions in the teammate report
    warning_phrases = [
        "out-of-scope",
        "out of scope",
        "intentionally skipped",
        "deferred",
        "partial coverage",
        "subset of",
        "core only",
        "manual validation",
        "follow-up",
    ]
    report_lower = completion_report.lower()
    scope_flags = [p for p in warning_phrases if p in report_lower]
    warning = None
    if scope_flags:
        warning = (
            f"Teammate completion report contains scope-flag phrases: {scope_flags}. "
            f"Do NOT accept this casting. Re-dispatch with explicit instruction to "
            f"complete the missing work. Build-green is necessary but NOT sufficient."
        )
    elif missing_citations:
        warning = (
            f"Completion report is missing citations for "
            f"{len(missing_citations)} requirement(s): {', '.join(missing_citations)}. "
            f"Every requirement ID in the casting's <spec_requirements> block must "
            f"have a corresponding citation in the completion report proving where it "
            f"was implemented. Do NOT accept this casting. Re-dispatch with "
            f"instruction: 'For each requirement ID (US-N, FR-N, etc.) cite the exact "
            f"path#Symbol where it was implemented.' Build-green is necessary but NOT sufficient."
        )
    elif unresolved_cites:
        # AC-006 — an unresolvable symbol is a defect, not a warning about
        # formatting. Named individually so the teammate can fix the cite
        # rather than re-scan the whole report.
        warning = (
            f"Completion report cites {len(unresolved_cites)} symbol(s) that resolve "
            f"nowhere in the tree: "
            f"{', '.join(c['cite'] for c in unresolved_cites)}. "
            f"Each is a defect from the mechanical symbol-resolution guard. Do NOT "
            f"accept this casting. Re-dispatch with instruction: 'Every path#Symbol "
            f"cite must name a symbol that exists in the named file.' A stale :line "
            f"hint is NOT the problem — the line component is never judged, so do not "
            f"run a cite-refresh sweep."
        )

    # Record the acceptance attempt as a handoff entry.
    # Use the raw path string to avoid macOS /tmp ↔ /private/tmp symlink
    # mismatches during relative_to computation.
    foundry_handoff(
        event="acceptance",
        source=f"castings/casting-{casting_id}-prompt.md",
        destination=f"casting-{casting_id}-accepted",
        source_reread=True,  # the MCP tool enforces it by requiring fresh hashes
        summary=f"casting {casting_id} acceptance check",
        information_loss=", ".join(scope_flags) if scope_flags else "",
        project_root=project_root,
    )

    return {
        "ok": warning is None,
        "casting_id": casting_id,
        "acceptance_criteria": acs,
        "requirement_ids": casting_req_ids,
        "missing_citations": missing_citations,
        "unresolved_symbol_cites": unresolved_cites,
        "must_verify": [
            f"Every AC above has a corresponding artifact/behavior in the completion report",
            f"Every requirement ID has a path#Symbol or file:line citation in the completion report",
            f"Every path#Symbol cite resolves in the tree (a stale :line hint is never a finding)",
            f"Build is green AND tests pass",
            f"No scope-flag phrases in the completion report",
            f"Research compliance check (if research_context applies): each recommendation honored",
        ],
        "warning": warning,
        "evidence_verdict": evidence_verdict,
        "evidence_provenance": evidence_provenance,
        "evidence_stream_skips": evidence_stream_skips,
        "evidence_spec_path": (
            str(evidence_spec_path) if evidence_spec_path is not None else None
        ),
    }
