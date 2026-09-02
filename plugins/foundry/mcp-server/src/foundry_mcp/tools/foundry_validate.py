"""Foundry casting validation — 9-dimension quality gate before CAST phase.

Validates that castings will deliver the spec before any building starts.
A 5-minute validation saves hours of GRIND cycles.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.tools.foundry_orchestrator import _artifact_guard, _load_json
# D-134: the SHARED nested-shape validator, so "unusable manifest" means one
# thing in every module that reads castings/manifest.json. A module-top import
# is safe here — validate -> spawn -> orchestrator has no cycle — unlike in
# foundry_orchestrator, which foundry_spawn imports and which therefore reaches
# the same validator through a lazy in-function import.
from foundry_mcp.tools.foundry_spawn import _manifest_shape_problem
from foundry_mcp.tools.foundry_state import (
    document_refusal,
    get_run_dir,
    read_document,
    read_text_file,
)


def _fingerprint_inputs(fdir: Path, manifest: dict) -> dict:
    """Hash the inputs that validator dimensions depend on.

    Returns {spec_hash, manifest_hash, castings: {id: {prompt_hash, manifest_entry_hash}}}.
    Any change in these hashes invalidates the cached validation result
    for the affected casting (or all castings when spec/manifest-level
    inputs change).
    """
    spec_path = fdir / "spec.md"
    spec_bytes = spec_path.read_bytes() if spec_path.exists() else b""
    spec_hash = hashlib.sha256(spec_bytes).hexdigest()[:16]

    shared_fields = {
        "spec_type": manifest.get("spec_type"),
        "migration_source_root": manifest.get("migration_source_root"),
        "migration_destination_root": manifest.get("migration_destination_root"),
        "file_change_map": manifest.get("file_change_map"),
    }
    manifest_hash = hashlib.sha256(
        json.dumps(shared_fields, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    castings_fp: dict[str, dict] = {}
    if _manifest_shape_problem(manifest) is not None:
        return {"spec_hash": spec_hash, "manifest_hash": manifest_hash, "castings": {}}
    for c in manifest.get("castings", []):
        cid = str(c.get("id"))
        prompt_path = fdir / "castings" / f"casting-{cid}-prompt.md"
        prompt_bytes = prompt_path.read_bytes() if prompt_path.exists() else b""
        entry_bytes = json.dumps(c, sort_keys=True).encode("utf-8")
        castings_fp[cid] = {
            "prompt_hash": hashlib.sha256(prompt_bytes).hexdigest()[:16],
            "manifest_entry_hash": hashlib.sha256(entry_bytes).hexdigest()[:16],
        }

    return {"spec_hash": spec_hash, "manifest_hash": manifest_hash, "castings": castings_fp}


def _load_validate_cache(fdir: Path) -> dict:
    cache_path = fdir / ".validate-cache.json"
    if not cache_path.exists():
        return {}
    return read_document(cache_path)[0]


def _save_validate_cache(fdir: Path, cache: dict) -> None:
    try:
        (fdir / ".validate-cache.json").write_text(
            json.dumps(cache, indent=2), encoding="utf-8"
        )
    except OSError:
        pass


def foundry_validate_castings(
    project_root: str = ".",
) -> dict:
    """Validate castings against the spec across 9 dimensions.

    Returns:
        {
            "passed": bool,
            "dimensions": {
                "requirement_coverage": {"ok": bool, "issues": [...]},
                "casting_completeness": {"ok": bool, "issues": [...]},
                "dependency_correctness": {"ok": bool, "issues": [...]},
                "key_links_planned": {"ok": bool, "issues": [...]},
                "scope_sanity": {"ok": bool, "issues": [...]},
                "research_integration": {"ok": bool, "issues": [...]},
                "prompt_fidelity": {"ok": bool, "issues": [...]},
                "migration_coverage": {"ok": bool, "issues": [...]},
                "spec_structure": {"ok": bool, "issues": [...]},
            },
            "issues": [...],
            "revision_hints": [...],
        }
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"passed": False, "error": "No active foundry run"}

    if (corrupt := _artifact_guard(fdir)):
        return {"passed": False, **corrupt}

    manifest_path = fdir / "castings" / "manifest.json"
    if not manifest_path.exists():
        return {"passed": False, "error": "No manifest.json found"}

    # D-130's class, found by the package-wide scan. Both reads below were
    # `json.loads(...read_text(...))`, so a corrupt manifest.json or state.json
    # raised out of Foundry-Validate-Castings instead of naming the file. The
    # guard above reports both by name; these loads are now total.
    manifest = _load_json(manifest_path)
    # D-134: the RECORDS, not just the container. `_load_json` guards the
    # document being a dict and `.get("castings", [])` guards the key being
    # present — neither guards what the members inside are, so a manifest whose
    # `castings` is a string reached `c.get(...)` below and raised
    # AttributeError out of Foundry-Validate-Castings. The house refusal names
    # the file and carries `corrupt_artifacts`, the way every other artifact
    # refusal on this surface does.
    if (records := _manifest_shape_problem(manifest)) is not None:
        return {
            "passed": False,
            "error": (
                f"Run artifacts cannot be read: {records}. This tool refuses "
                f"rather than acting on a document it had to guess at."
            ),
            "hint": (
                "Re-run F0.5 DECOMPOSE. The manifest is a JSON object whose "
                "`castings` and `waves` are lists of objects — each casting "
                "carrying an `id`, each wave a `wave` number."
            ),
            "corrupt_artifacts": [records],
        }
    castings = manifest.get("castings", [])
    spec_type = (manifest.get("spec_type") or "GREENFIELD").upper()

    if not castings:
        return {"passed": False, "error": "No castings in manifest"}

    # Short-circuit: if everything is byte-identical to the last passing
    # run and the prior verdict was pass, return cached result instead of
    # re-running all 10 dimensions. Reject→fix→revalidate loops fix only
    # a subset of castings per iteration; when the lead accidentally
    # re-calls Foundry-Validate-Castings without any code change (happens
    # on retry paths), the cache short-circuits to near-zero cost.
    _started_wall = datetime.now(timezone.utc)
    fingerprints = _fingerprint_inputs(fdir, manifest)
    cache = _load_validate_cache(fdir)
    cached_result = cache.get("last_pass")
    if cached_result and cached_result.get("fingerprints") == fingerprints:
        return {
            **cached_result["result"],
            "cache": {"hit": True, "cached_at": cached_result.get("cached_at")},
        }

    # Load spec to extract requirements
    spec_path = fdir / "spec.md"
    state = _load_json(fdir / "state.json")
    if not spec_path.exists():
        sp = state.get("spec_path", "")
        if sp:
            candidate = Path(project_root) / sp
            if candidate.exists():
                spec_path = candidate

    # D-145: this read is the one that leaves the run directory. When the run
    # dir holds no spec.md the fallback above resolves `state["spec_path"]`
    # against the project root — which is the LIVE shape of this very run — and
    # `_artifact_guard`'s rglob could not reach the result, so an unguarded
    # `read_text` here raised UnicodeDecodeError across the MCP boundary.
    # `read_text_file` is total ("" for an absent file, which is what the old
    # `if exists()` produced) and NAMES the file when it cannot be read.
    spec_text, spec_problem = read_text_file(spec_path)
    if spec_problem is not None:
        return {"passed": False, **document_refusal(spec_path, spec_problem)}
    spec_req_ids = set(re.findall(r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b", spec_text))

    # Check for research artifacts
    research_dir = fdir / "research"
    has_research = research_dir.exists() and any(research_dir.iterdir()) if research_dir.exists() else False

    issues: list[dict] = []
    revision_hints: list[str] = []
    dimensions: dict[str, dict] = {}

    # ── Dimension 1: Requirement Coverage ──
    covered_reqs: set[str] = set()
    for c in castings:
        spec_text_field = c.get("spec_text", "")
        casting_reqs = set(re.findall(r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b", spec_text_field))
        covered_reqs.update(casting_reqs)
        # Also check observable truths text
        for truth in c.get("observable_truths", []):
            truth_reqs = set(re.findall(r"\b(?:US|FR|NFR|AC|VC|IR|TR)-\d+(?:\.\d+)?\b", truth))
            covered_reqs.update(truth_reqs)

    uncovered = spec_req_ids - covered_reqs
    dim1_ok = len(uncovered) == 0
    dim1_issues = []
    if uncovered:
        dim1_issues.append({"type": "uncovered_requirements", "ids": sorted(uncovered)})
        issues.append({"dimension": "requirement_coverage", "severity": "error",
                       "message": f"{len(uncovered)} requirements not in any casting: {', '.join(sorted(uncovered))}"})
        revision_hints.append(f"Add uncovered requirements to appropriate castings: {', '.join(sorted(uncovered))}")
    dimensions["requirement_coverage"] = {"ok": dim1_ok, "issues": dim1_issues,
                                          "covered": len(covered_reqs), "total": len(spec_req_ids)}

    # ── Dimension 2: Casting Completeness ──
    dim2_issues = []
    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")

        # Check observable truths
        truths = c.get("observable_truths", [])
        if len(truths) < 3:
            dim2_issues.append({"casting": cid, "issue": f"Only {len(truths)} observable truths (min 3)", "title": title})
            revision_hints.append(f"Casting #{cid} '{title}': add more observable truths (currently {len(truths)}, need 3+)")

        # Check must_haves if present
        must_haves = c.get("must_haves", {})
        if must_haves:
            mh_truths = must_haves.get("truths", [])
            mh_artifacts = must_haves.get("artifacts", [])
            mh_links = must_haves.get("key_links", [])
            if len(mh_truths) < 1:
                dim2_issues.append({"casting": cid, "issue": "must_haves.truths is empty", "title": title})
            if len(mh_artifacts) < 1:
                dim2_issues.append({"casting": cid, "issue": "must_haves.artifacts is empty", "title": title})
            if len(mh_links) < 1:
                dim2_issues.append({"casting": cid, "issue": "must_haves.key_links is empty", "title": title})

    dim2_ok = len(dim2_issues) == 0
    if dim2_issues:
        issues.append({"dimension": "casting_completeness", "severity": "warning",
                       "message": f"{len(dim2_issues)} completeness issues found"})
    dimensions["casting_completeness"] = {"ok": dim2_ok, "issues": dim2_issues}

    # ── Dimension 3: Dependency Correctness ──
    dim3_issues = []
    file_to_casting: dict[str, list] = {}
    for c in castings:
        cid = c.get("id", "?")
        for f in c.get("key_files", []):
            file_to_casting.setdefault(f, []).append(cid)

    overlaps = {f: cids for f, cids in file_to_casting.items() if len(cids) > 1}
    if overlaps:
        for f, cids in overlaps.items():
            dim3_issues.append({"file": f, "castings": cids, "issue": "File claimed by multiple castings"})
            revision_hints.append(f"File '{f}' is in castings {cids} — move to one casting or split")
        issues.append({"dimension": "dependency_correctness", "severity": "error",
                       "message": f"{len(overlaps)} file overlaps between castings"})

    dim3_ok = len(dim3_issues) == 0
    dimensions["dependency_correctness"] = {"ok": dim3_ok, "issues": dim3_issues}

    # ── Dimension 4: Key Links Planned ──
    dim4_issues = []
    all_artifacts: set[str] = set()
    all_link_targets: set[str] = set()
    # Expected must_haves shapes (what Decompose should emit):
    #   - artifacts:  [{"path": "src/foo.ts", "min_lines": 20}, ...]
    #   - key_links:  [{"from": "src/a.ts", "to": "src/b.ts"}, ...]
    #                 (each link names the two endpoints — the "from" source
    #                  and the "to" target — of one wiring connection).
    # Decompose occasionally emits a plain STRING instead (a free-text link or
    # artifact description like "LoginForm -> /api/login"). A string has no
    # .get(), so the reads below would raise
    #   AttributeError: 'str' object has no attribute 'get'
    # and the entire 10-dimension report would fail to render at F0.9. Guard
    # each read with isinstance(...): a dict entry behaves EXACTLY as before,
    # while a string entry is accepted as a plain description and recorded as a
    # NON-BLOCKING warning (severity "warning", kept out of dim4_ok) that names
    # the casting and the offending entry.
    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")
        must_haves = c.get("must_haves", {})
        for art in must_haves.get("artifacts", []):
            if isinstance(art, dict):
                all_artifacts.add(art.get("path", ""))
            else:
                all_artifacts.add(str(art))
                dim4_issues.append({
                    "casting": cid, "title": title, "severity": "warning",
                    "issue": (
                        f"Casting #{cid} '{title}': must_haves.artifacts entry is a "
                        f"string, not a {{path: ...}} object ({art!r}) — treated as a "
                        f"plain artifact description"
                    ),
                })
        for link in must_haves.get("key_links", []):
            if isinstance(link, dict):
                all_link_targets.add(link.get("from", ""))
                all_link_targets.add(link.get("to", ""))
            else:
                dim4_issues.append({
                    "casting": cid, "title": title, "severity": "warning",
                    "issue": (
                        f"Casting #{cid} '{title}': must_haves.key_links entry is a "
                        f"string, not a {{from, to}} object ({link!r}) — treated as a "
                        f"plain link description"
                    ),
                })

    # Check if any casting has artifacts but no key_links (isolated)
    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")
        must_haves = c.get("must_haves", {})
        artifacts = must_haves.get("artifacts", [])
        links = must_haves.get("key_links", [])
        if len(artifacts) >= 2 and len(links) == 0:
            dim4_issues.append({"casting": cid, "title": title,
                               "issue": f"Has {len(artifacts)} artifacts but no key_links — isolated"})
            revision_hints.append(f"Casting #{cid} '{title}': add key_links showing how artifacts connect")

    # String-entry warnings are non-blocking: exclude them from dim4_ok and
    # surface them as a warning-severity entry in the top-level issues list,
    # which the overall `passed` computation ignores (it fails only on
    # severity=="error"). Non-warning issues (isolated castings) keep their
    # prior weight, so dict-entry behavior is unchanged.
    dim4_warnings = [i for i in dim4_issues if i.get("severity") == "warning"]
    dim4_errors = [i for i in dim4_issues if i.get("severity") != "warning"]
    dim4_ok = len(dim4_errors) == 0
    if dim4_warnings:
        issues.append({
            "dimension": "key_links_planned",
            "severity": "warning",
            "message": (
                f"{len(dim4_warnings)} must_haves key_links/artifacts entr"
                f"{'y is a string' if len(dim4_warnings) == 1 else 'ies are strings'}, "
                f"not object(s) — treated as plain description(s) (non-blocking)"
            ),
        })
    dimensions["key_links_planned"] = {
        "ok": dim4_ok, "issues": dim4_issues, "warnings": len(dim4_warnings),
    }

    # ── Dimension 5: Scope Sanity ──
    dim5_issues = []
    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")
        kf = len(c.get("key_files", []))
        if kf > 8:
            dim5_issues.append({"casting": cid, "title": title, "key_files": kf,
                               "issue": f"Too many key_files ({kf} > 8)"})
            revision_hints.append(f"Casting #{cid} '{title}': split into smaller castings (currently {kf} files)")

        # Check observable truths are user-facing
        truths = c.get("observable_truths", [])
        impl_detail_patterns = [
            r"import\b", r"export\b", r"function\b", r"class\b",
            r"instanceof", r"typeof", r"\.ts\b", r"\.js\b",
        ]
        non_user_facing = []
        for truth in truths:
            for pattern in impl_detail_patterns:
                if re.search(pattern, truth, re.IGNORECASE):
                    non_user_facing.append(truth)
                    break
        if non_user_facing:
            dim5_issues.append({"casting": cid, "title": title,
                               "issue": f"{len(non_user_facing)} truths look like implementation details, not user-facing behaviors",
                               "examples": non_user_facing[:3]})

    dim5_ok = len(dim5_issues) == 0
    dimensions["scope_sanity"] = {"ok": dim5_ok, "issues": dim5_issues}

    # ── Dimension 6: Research Integration ──
    dim6_issues = []
    if has_research:
        castings_with_research = sum(1 for c in castings if c.get("research_context"))
        if castings_with_research == 0:
            dim6_issues.append({"issue": "Research artifacts exist but no casting references them"})
            revision_hints.append("Research was conducted but no casting has research_context — link relevant findings")
            issues.append({"dimension": "research_integration", "severity": "warning",
                          "message": "Research exists but no casting references it"})

    dim6_ok = len(dim6_issues) == 0
    dimensions["research_integration"] = {"ok": dim6_ok, "issues": dim6_issues}

    # ── Dimension 7: Prompt Fidelity ──
    #
    # Every casting must have a pre-authored teammate prompt file at
    # `castings/casting-{id}-prompt.md`. The prompt MUST contain the spec
    # requirements for this casting as a literal substring of the master
    # spec.md — no paraphrasing allowed. Plans are prompts: authored once
    # from the spec, handed directly to teammates without lead re-translation.
    #
    # Sub-check 7e: every prompt must also contain a <global_invariants> block
    # whose content is byte-identical across all castings AND matches
    # manifest.global_invariants verbatim AND is a verbatim substring of
    # spec.md. This propagates cross-cutting rules (auth, validation, naming,
    # security) to every teammate without relying on decompose's judgment
    # about which casting "needs" which rule.
    dim7_issues = []
    castings_dir = fdir / "castings"
    normalized_spec = _normalize(spec_text)
    manifest_invariants = manifest.get("global_invariants", "") or ""
    normalized_manifest_invariants = _normalize(manifest_invariants)
    manifest_rules = manifest.get("mandatory_rules", "") or ""
    normalized_manifest_rules = _normalize(manifest_rules)
    # Track per-casting invariant hashes so we can verify byte-identical
    # propagation across every casting prompt.
    import hashlib as _hashlib
    invariant_hashes: dict = {}  # casting_id -> sha256 of normalized block
    rules_hashes: dict = {}  # casting_id -> sha256 of normalized mandatory_rules block

    for c in castings:
        cid = c.get("id", "?")
        title = c.get("title", "Untitled")
        prompt_path = castings_dir / f"casting-{cid}-prompt.md"

        # 7a: the prompt file must exist
        if not prompt_path.exists():
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "missing_prompt_file",
                "detail": f"casting-{cid}-prompt.md does not exist",
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': decompose must write castings/casting-{cid}-prompt.md. "
                f"Re-run F0.5 DECOMPOSE."
            )
            continue

        # D-145's second unguarded read in this module. It is covered TODAY
        # only because a casting prompt happens to live inside the run dir,
        # where the artifact guard's rglob reaches it — which is a fact about
        # where the file sits, not a property of this reader. Made total, so
        # the reader holds on its own merits wherever the path resolves.
        prompt_text, prompt_problem = read_text_file(prompt_path)
        if prompt_problem is not None:
            return {"passed": False, **document_refusal(prompt_path, prompt_problem)}

        if not prompt_text.strip():
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "empty_prompt_file",
                "detail": f"casting-{cid}-prompt.md is empty",
            })
            continue

        # 7b: the prompt must contain a <spec_requirements> block
        spec_block = _extract_spec_block(prompt_text)
        if spec_block is None:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "missing_spec_block",
                "detail": (
                    f"casting-{cid}-prompt.md has no <spec_requirements>...</spec_requirements> "
                    f"section. The spec requirements must be included verbatim in that block."
                ),
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': add a <spec_requirements> block containing "
                f"the verbatim spec text for this casting's ACs."
            )
            continue

        # 7c: every non-trivial line in the spec block must appear verbatim in spec.md
        if not normalized_spec:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "spec_unreadable",
                "detail": "spec.md could not be read; cannot verify substring integrity",
            })
            continue

        drift_lines = _find_drift(spec_block, normalized_spec)
        if drift_lines:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "spec_drift_detected",
                "detail": (
                    f"{len(drift_lines)} line(s) in the prompt's <spec_requirements> block do not "
                    f"appear verbatim in spec.md"
                ),
                "examples": drift_lines[:3],
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': the <spec_requirements> block must be a literal copy-paste "
                f"from spec.md. Paraphrasing and summarizing are forbidden. Re-run F0.5 DECOMPOSE and "
                f"copy spec text character-for-character."
            )

        # 7d: forbidden scope-cutting phrases.
        # teammate.md lives in foundry:teammate's system prompt (not inlined
        # into casting-{id}-prompt.md), so the file contains only decompose-
        # authored content — mandatory_rules block, global_invariants block,
        # spec_requirements block, metadata, classification. All of that
        # should be scanned for scope-cutting language.
        forbidden_found = _find_forbidden_phrases(prompt_text)
        if forbidden_found:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "forbidden_scope_phrase",
                "detail": f"prompt contains scope-cutting language",
                "phrases": forbidden_found,
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': remove forbidden phrases from the prompt "
                f"({', '.join(repr(p) for p in forbidden_found[:3])}). These phrases silently "
                f"authorize scope cuts and are banned from teammate prompts."
            )

        # 7e: global_invariants block propagation.
        # Required unconditionally: every prompt must contain the block so
        # F0.9 can verify uniform propagation. If manifest_invariants is
        # empty, an empty block is still required (the block's presence is
        # what enables mechanical verification across castings).
        invariant_block = _extract_invariants_block(prompt_text)
        if invariant_block is None:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "missing_global_invariants_block",
                "detail": (
                    f"casting-{cid}-prompt.md has no <global_invariants>...</global_invariants> "
                    f"section. Every casting prompt must contain this block so cross-cutting "
                    f"rules propagate uniformly to every teammate."
                ),
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': add a <global_invariants> block containing "
                f"manifest.global_invariants verbatim. If manifest.global_invariants is empty, "
                f"the block should still exist but be empty."
            )
        else:
            normalized_block = _normalize(invariant_block)
            # Hash the normalized block so we can detect drift across castings.
            h = _hashlib.sha256(normalized_block.encode("utf-8")).hexdigest()[:16]
            invariant_hashes[cid] = h

            # 7e.1: block content must match manifest.global_invariants.
            # Dim 9 separately verifies manifest↔spec fidelity, so chaining
            # 7e.1 with Dim 9 gives us transitive spec↔casting fidelity.
            if normalized_block != normalized_manifest_invariants:
                dim7_issues.append({
                    "casting": cid,
                    "title": title,
                    "issue": "global_invariants_drift_from_manifest",
                    "detail": (
                        f"casting-{cid}-prompt.md's <global_invariants> block does not match "
                        f"manifest.global_invariants verbatim (after normalization). Decompose "
                        f"must paste the manifest's invariants character-for-character."
                    ),
                })
                revision_hints.append(
                    f"Casting #{cid} '{title}': re-copy manifest.global_invariants into the "
                    f"<global_invariants> block. Never paraphrase or summarize cross-cutting rules."
                )

        # 7g: mandatory_rules block propagation.
        # Every prompt must contain a <mandatory_rules> block — the CLAUDE.md /
        # AGENTS.md / .cursorrules imperatives propagated identically to every
        # casting. Same mechanics as 7e: byte-identical across castings, verbatim
        # from manifest.mandatory_rules. Presence required regardless of whether
        # the project has a CLAUDE.md (empty block is valid; absent block is not).
        rules_block = _extract_mandatory_rules_block(prompt_text)
        if rules_block is None:
            dim7_issues.append({
                "casting": cid,
                "title": title,
                "issue": "missing_mandatory_rules_block",
                "detail": (
                    f"casting-{cid}-prompt.md has no <mandatory_rules>...</mandatory_rules> "
                    f"section. Every casting prompt must contain this block so CLAUDE.md "
                    f"imperatives propagate uniformly to every teammate."
                ),
            })
            revision_hints.append(
                f"Casting #{cid} '{title}': add a <mandatory_rules> block containing "
                f"manifest.mandatory_rules verbatim. If the project has no CLAUDE.md, "
                f"the block should still exist but be empty."
            )
        else:
            normalized_rules_block = _normalize(rules_block)
            rules_h = _hashlib.sha256(normalized_rules_block.encode("utf-8")).hexdigest()[:16]
            rules_hashes[cid] = rules_h
            if normalized_rules_block != normalized_manifest_rules:
                dim7_issues.append({
                    "casting": cid,
                    "title": title,
                    "issue": "mandatory_rules_drift_from_manifest",
                    "detail": (
                        f"casting-{cid}-prompt.md's <mandatory_rules> block does not match "
                        f"manifest.mandatory_rules verbatim (after normalization). Decompose "
                        f"must paste the manifest's rules character-for-character."
                    ),
                })
                revision_hints.append(
                    f"Casting #{cid} '{title}': re-copy manifest.mandatory_rules into the "
                    f"<mandatory_rules> block. Never paraphrase or filter CLAUDE.md rules."
                )

    # 7e.3: block content must be byte-identical across EVERY casting.
    # Run this check after the per-casting loop so we have all hashes.
    if len(set(invariant_hashes.values())) > 1:
        hash_to_castings: dict = {}
        for cid, h in invariant_hashes.items():
            hash_to_castings.setdefault(h, []).append(cid)
        dim7_issues.append({
            "issue": "global_invariants_inconsistent_across_castings",
            "detail": (
                f"Different castings have different <global_invariants> blocks. "
                f"Every casting must contain byte-identical invariants."
            ),
            "groups": {h: cids for h, cids in hash_to_castings.items()},
        })
        revision_hints.append(
            "Different castings have different <global_invariants> content. Re-run decompose "
            "and propagate manifest.global_invariants byte-identical to every casting prompt."
        )

    # 7g.3: mandatory_rules must be byte-identical across every casting too.
    if len(set(rules_hashes.values())) > 1:
        hash_to_castings_r: dict = {}
        for cid, h in rules_hashes.items():
            hash_to_castings_r.setdefault(h, []).append(cid)
        dim7_issues.append({
            "issue": "mandatory_rules_inconsistent_across_castings",
            "detail": (
                f"Different castings have different <mandatory_rules> blocks. "
                f"Every casting must contain byte-identical CLAUDE.md rules."
            ),
            "groups": {h: cids for h, cids in hash_to_castings_r.items()},
        })
        revision_hints.append(
            "Different castings have different <mandatory_rules> content. Re-run decompose "
            "and propagate manifest.mandatory_rules byte-identical to every casting prompt."
        )

    # Sub-check 7m (Phase 8 / INTENT-01): intent-coverage.json present when
    # INTENT-01 not stream-skipped. Mirror of sub-check 7k's stream_skips
    # re-derivation discipline applied at the file-presence + manifest-summary
    # level. By-reference to 7k's roster derivation: if INTENT-01 is NOT in
    # manifest.stream_skips (i.e., the F0.5 step 2b roster routed it as an
    # active stream on a v2.1+ spec), F0.7 must have produced the matrix —
    # absence is itself a defect that fires INTENT_COVERAGE_RECORD_INCOMPLETE.
    # Defense-in-depth: 7k validates the roster derivation; 7m validates that
    # the active streams actually emitted their artifacts.
    intent_in_skips = any(
        (s.get("stream_id") if isinstance(s, dict) else None) == "INTENT-01"
        for s in manifest.get("stream_skips", []) or []
    )
    if not intent_in_skips:
        intent_coverage_path = fdir / "intent-coverage.json"
        if not intent_coverage_path.exists():
            dim7_issues.append({
                "issue": "intent_coverage_record_incomplete",
                "detail": (
                    f"INTENT_COVERAGE_RECORD_INCOMPLETE: intent-coverage.json missing at "
                    f"{intent_coverage_path}; INTENT-01 not in manifest.stream_skips so "
                    f"the F0.7 stream should have produced the matrix"
                ),
            })
            revision_hints.append(
                "INTENT_COVERAGE_RECORD_INCOMPLETE: re-run F0.7 INTENT-CARRIER to produce "
                "intent-coverage.json. INTENT-01 is an active stream on this spec_format_version."
            )
        elif "intent_coverage_summary" not in manifest:
            dim7_issues.append({
                "issue": "intent_coverage_record_incomplete",
                "detail": (
                    "INTENT_COVERAGE_RECORD_INCOMPLETE: intent-coverage.json present but "
                    "manifest.intent_coverage_summary missing; F0.7 marker stamping incomplete"
                ),
            })
            revision_hints.append(
                "INTENT_COVERAGE_RECORD_INCOMPLETE: re-run Foundry-Intent-Coverage to stamp "
                ".f07-intent-clean marker and append manifest.intent_coverage_summary."
            )

    dim7_ok = len(dim7_issues) == 0
    if not dim7_ok:
        issues.append({
            "dimension": "prompt_fidelity",
            "severity": "error",
            "message": f"{len(dim7_issues)} prompt fidelity issue(s) detected",
        })
    dimensions["prompt_fidelity"] = {"ok": dim7_ok, "issues": dim7_issues}

    # ── Dimension 8: Migration Coverage ──
    #
    # Only runs when spec_type is MIGRATION. For migration specs, every
    # casting MUST declare a coverage_list under must_haves enumerating
    # the source_file:symbol entries it is responsible for porting. Every
    # source symbol must be assigned to exactly one casting (no duplicates,
    # no gaps). This enforces the "full 1:1 coverage" invariant that
    # unambiguous migration specs require.
    dim8_issues = []
    if spec_type == "MIGRATION":
        per_casting_coverage: dict[int, list] = {}
        all_source_entries: dict[str, list] = {}  # entry -> [casting_ids]

        for c in castings:
            cid = c.get("id", "?")
            title = c.get("title", "Untitled")
            mh = c.get("must_haves", {})
            cov = mh.get("coverage_list", [])

            if not isinstance(cov, list) or not cov:
                dim8_issues.append({
                    "casting": cid,
                    "title": title,
                    "issue": "missing_coverage_list",
                    "detail": (
                        f"spec_type is MIGRATION but casting #{cid} has no "
                        f"must_haves.coverage_list. Migration specs require every "
                        f"casting to enumerate the source_file:symbol entries it ports."
                    ),
                })
                revision_hints.append(
                    f"Casting #{cid} '{title}': add a coverage_list array under must_haves "
                    f"with every source_file:symbol this casting must port (1:1)."
                )
                continue

            per_casting_coverage[cid] = cov
            for entry in cov:
                if not isinstance(entry, str):
                    dim8_issues.append({
                        "casting": cid,
                        "title": title,
                        "issue": "invalid_coverage_entry",
                        "detail": f"coverage_list must contain strings like 'path/to/file.go:TestSymbolName', got {entry!r}",
                    })
                    continue
                all_source_entries.setdefault(entry, []).append(cid)

        # Detect duplicates — same source entry claimed by multiple castings
        dupes = {e: cids for e, cids in all_source_entries.items() if len(cids) > 1}
        for entry, cids in dupes.items():
            dim8_issues.append({
                "issue": "duplicate_coverage_entry",
                "entry": entry,
                "castings": cids,
                "detail": f"source entry '{entry}' is claimed by castings {cids}; assign to exactly one",
            })
            revision_hints.append(
                f"Source entry '{entry}' is in multiple castings ({cids}) — assign to one casting only."
            )

        # Completeness: if the spec declares a source_inventory, verify every
        # inventory entry appears in some casting's coverage_list. The Forge
        # migration mode writes this inventory to the manifest under
        # `source_inventory` after R2 INTERVIEW.
        source_inventory = manifest.get("source_inventory", [])
        if source_inventory:
            claimed = set(all_source_entries.keys())
            missing = [e for e in source_inventory if e not in claimed]
            for entry in missing:
                dim8_issues.append({
                    "issue": "uncovered_source_entry",
                    "entry": entry,
                    "detail": f"source inventory entry '{entry}' is not covered by any casting",
                })
            if missing:
                revision_hints.append(
                    f"{len(missing)} source inventory entries are not in any casting's "
                    f"coverage_list. First 3: {missing[:3]}. Either assign them or justify omission "
                    f"in the spec."
                )

    dim8_ok = len(dim8_issues) == 0
    if not dim8_ok:
        issues.append({
            "dimension": "migration_coverage",
            "severity": "error",
            "message": f"{len(dim8_issues)} migration coverage issue(s) detected",
        })
    dimensions["migration_coverage"] = {
        "ok": dim8_ok,
        "issues": dim8_issues,
        "spec_type": spec_type,
        "active": spec_type == "MIGRATION",
    }

    # ── Dimension 9: Spec Structure ──
    #
    # Validates the master spec.md has the minimum structure foundry needs
    # to prevent drift:
    #   9a (error): spec contains at least one tagged requirement ID
    #       (US-N, FR-N, NFR-N, AC-N, VC-N, IR-N, TR-N). Without IDs,
    #       Dimension 1 coverage tracking is impossible and Phase 3
    #       citation checks cannot be enforced.
    #   9b (warning): spec has a `## Global Invariants` section (or
    #       <global_invariants> block). Missing → decompose has nothing
    #       to propagate, so cross-cutting rules must be embedded per
    #       casting which reintroduces tunnel-vision drift. Warning-only
    #       to allow gradual adoption on existing specs.
    #
    # 9a also confirms that manifest.global_invariants, if present, was
    # populated from the spec's section — catches decompose inventing
    # invariants. (The per-casting verbatim check in 7e already handles
    # this, but we surface it here too for clearer diagnostics.)
    dim9_issues = []

    if not spec_text:
        dim9_issues.append({
            "severity": "error",
            "issue": "spec_unreadable",
            "detail": "spec.md could not be read — cannot validate spec structure",
        })
    else:
        # 9a: requirement IDs
        if not spec_req_ids:
            dim9_issues.append({
                "severity": "error",
                "issue": "no_tagged_requirements",
                "detail": (
                    "spec.md contains no tagged requirement IDs. Foundry requires "
                    "every requirement to be tagged with an ID like US-1, FR-2, "
                    "NFR-3, AC-4, VC-5, IR-6, or TR-7 so coverage and citations "
                    "can be tracked mechanically. Add IDs to every requirement, "
                    "or re-generate the spec via Forge/Lisa."
                ),
            })
            revision_hints.append(
                "Add tagged requirement IDs to spec.md (US-N, FR-N, NFR-N, AC-N, etc.). "
                "Without IDs, Foundry cannot track coverage or enforce citations."
            )

        # 9b: global invariants section (warning-only for backward compat)
        spec_invariants = _extract_spec_invariants_section(spec_text)
        if not spec_invariants:
            dim9_issues.append({
                "severity": "warning",
                "issue": "no_global_invariants_section",
                "detail": (
                    "spec.md has no '## Global Invariants' section. Cross-cutting "
                    "rules (auth, validation, naming, error handling, security) "
                    "must be propagated to every casting to prevent tunnel-vision "
                    "drift. Add a '## Global Invariants' section listing rules "
                    "that apply to every casting regardless of its slice."
                ),
            })
            revision_hints.append(
                "Add a `## Global Invariants` section to spec.md listing cross-cutting "
                "rules that apply to every casting (auth, validation, naming, error "
                "handling). These will be propagated verbatim to every teammate prompt."
            )
        else:
            # If the spec HAS invariants, manifest.global_invariants must
            # match them verbatim. Surface the check here too for clearer
            # diagnostics than Dimension 7's per-casting drift messages.
            if not manifest_invariants:
                dim9_issues.append({
                    "severity": "error",
                    "issue": "manifest_invariants_missing",
                    "detail": (
                        "spec.md declares a '## Global Invariants' section but "
                        "manifest.global_invariants is empty. Decompose must copy "
                        "the section verbatim into the manifest."
                    ),
                })
                revision_hints.append(
                    "Copy spec.md's `## Global Invariants` section verbatim into "
                    "manifest.global_invariants (top-level field)."
                )
            elif _normalize(spec_invariants) != normalized_manifest_invariants:
                dim9_issues.append({
                    "severity": "error",
                    "issue": "manifest_invariants_drift",
                    "detail": (
                        "manifest.global_invariants does not match spec.md's "
                        "'## Global Invariants' section verbatim (after normalization). "
                        "Decompose must paste it character-for-character."
                    ),
                })
                revision_hints.append(
                    "Re-copy spec.md's `## Global Invariants` section into "
                    "manifest.global_invariants. Never paraphrase cross-cutting rules."
                )

    dim9_errors = [i for i in dim9_issues if i.get("severity") == "error"]
    dim9_warnings = [i for i in dim9_issues if i.get("severity") == "warning"]
    dim9_ok = len(dim9_errors) == 0
    if dim9_errors:
        issues.append({
            "dimension": "spec_structure",
            "severity": "error",
            "message": f"{len(dim9_errors)} spec structure error(s) detected",
        })
    if dim9_warnings:
        issues.append({
            "dimension": "spec_structure",
            "severity": "warning",
            "message": f"{len(dim9_warnings)} spec structure warning(s): add `## Global Invariants` section to prevent cross-cutting drift",
        })
    dimensions["spec_structure"] = {
        "ok": dim9_ok,
        "issues": dim9_issues,
        "errors": len(dim9_errors),
        "warnings": len(dim9_warnings),
    }

    # ── Dimension 10: File Change Map ↔ key_files cross-check ──
    #
    # The spec's `## File Change Map` section enumerates every file that
    # should be modified or created. Every casting declares its `key_files`
    # boundary — the files the teammate is allowed to touch. These two MUST
    # cross-check:
    #
    #   - Every file in the File Change Map MUST appear in exactly one
    #     casting's key_files (else the change is unimplementable — no
    #     teammate can reach it). ERROR.
    #   - Files in some casting's key_files but NOT in the File Change Map
    #     are flagged as scope creep (warning) — the teammate has access to
    #     a file the spec didn't authorize them to change.
    #
    # The check is skipped if the spec has no File Change Map section (older
    # specs or pure-doc specs). Current Forge templates always emit one.
    dim10_issues = []
    map_files = _extract_file_change_map_files(spec_text)

    if not map_files:
        dimensions["file_change_map_coverage"] = {
            "ok": True,
            "issues": [],
            "active": False,
            "reason": "spec has no File Change Map section (or none parseable)",
        }
    else:
        # Build {file: [casting_ids]} from key_files (already normalized
        # via _normalize_file_path so map_files and key_files compare
        # apples-to-apples).
        casting_files: dict[str, list] = {}
        for c in castings:
            cid = c.get("id", "?")
            for kf in c.get("key_files", []):
                normalized = _normalize_file_path(kf)
                if normalized:
                    casting_files.setdefault(normalized, []).append(cid)

        # Check 10a: every File Change Map entry is in some casting
        unimplementable = sorted(map_files - set(casting_files.keys()))
        for path in unimplementable:
            dim10_issues.append({
                "severity": "error",
                "issue": "file_change_map_orphan",
                "file": path,
                "detail": (
                    f"spec.md File Change Map declares '{path}' must change, "
                    f"but no casting has it in key_files. No teammate will "
                    f"reach this file — the change is unimplementable as "
                    f"sliced. Either add '{path}' to a casting's key_files, "
                    f"or remove it from the File Change Map if it shouldn't "
                    f"actually change."
                ),
            })
        if unimplementable:
            issues.append({
                "dimension": "file_change_map_coverage",
                "severity": "error",
                "message": (
                    f"{len(unimplementable)} file(s) in spec's File Change "
                    f"Map are not in any casting's key_files (unimplementable)"
                ),
            })
            revision_hints.append(
                f"Decompose missed {len(unimplementable)} files from the "
                f"File Change Map. Either widen a casting's key_files to "
                f"include them, add a new casting that owns them, or remove "
                f"them from the File Change Map. First 5: "
                f"{unimplementable[:5]}"
            )

        # Check 10b: scope creep — castings have files not in the map
        # (warning, not error — sometimes castings legitimately touch
        # adjacent files like test fixtures or import sites)
        scope_creep = sorted(set(casting_files.keys()) - map_files)
        for path in scope_creep[:20]:  # cap to avoid noise
            cids = casting_files[path]
            dim10_issues.append({
                "severity": "warning",
                "issue": "file_change_map_scope_creep",
                "file": path,
                "castings": cids,
                "detail": (
                    f"casting(s) {cids} declare '{path}' in key_files but "
                    f"the spec's File Change Map does not list it. Either "
                    f"the spec is incomplete (add the file to the map and "
                    f"explain why it changes) or the casting is overreaching "
                    f"(remove from key_files)."
                ),
            })
        if scope_creep:
            issues.append({
                "dimension": "file_change_map_coverage",
                "severity": "warning",
                "message": (
                    f"{len(scope_creep)} file(s) are in casting key_files "
                    f"but not in spec's File Change Map (potential scope creep)"
                ),
            })

        dim10_errors = [i for i in dim10_issues if i.get("severity") == "error"]
        dimensions["file_change_map_coverage"] = {
            "ok": len(dim10_errors) == 0,
            "issues": dim10_issues,
            "active": True,
            "map_files": len(map_files),
            "covered": len(map_files - set(casting_files.keys())),
            "scope_creep": len(scope_creep),
        }

    # ── Overall result ──
    # Fail on errors, warn on warnings
    error_count = sum(1 for i in issues if i.get("severity") == "error")
    passed = error_count == 0

    elapsed_ms = int((datetime.now(timezone.utc) - _started_wall).total_seconds() * 1000)
    result_payload = {
        "passed": passed,
        "dimensions": dimensions,
        "issues": issues,
        "revision_hints": revision_hints,
        "summary": {
            "castings": len(castings),
            "spec_requirements": len(spec_req_ids),
            "covered_requirements": len(covered_reqs),
            "error_count": error_count,
            "warning_count": len(issues) - error_count,
            "elapsed_ms": elapsed_ms,
        },
    }

    # Pass-marker lets foundry_next_action stamp F0.9 VALIDATE end time;
    # invalidated on fail so the marker only reflects the latest verdict.
    pass_marker = fdir / ".validate-passed"
    if passed:
        pass_marker.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")
        _save_validate_cache(
            fdir,
            {
                "last_pass": {
                    "fingerprints": fingerprints,
                    "result": result_payload,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
    else:
        pass_marker.unlink(missing_ok=True)

    return {**result_payload, "cache": {"hit": False}}


# ── Helpers for Dimension 7: Prompt Fidelity ──────────────────────────


_FORBIDDEN_PHRASES = [
    # Scope-cutting patterns
    "pick the core",
    "pick the most important",
    "don't port every",
    "do not port every",
    "skip the edge cases",
    "skip the edge case",
    "core coverage",
    "main cases",
    "the important ones",
    "follow-up pr",
    "follow up pr",
    "user will validate manually",
    "user will manually validate",
    "user will confirm later",
    "validate equivalence manually",
    "intentionally out-of-scope",
    "intentionally out of scope",
    "reduced scope",
    "target line count",
    "target ~",
    "aim for ~",
    "keep it under",
    # Hedge patterns
    "sufficient coverage",
    "equivalent to legacy for the main",
    "prove the framework is sufficient",
]


def _normalize(text: str) -> str:
    """Strip markdown formatting and collapse whitespace so substring
    matching compares meaningful content rather than formatting.

    Removes:
      - Leading list markers (`-`, `*`, `+`, `1.`, etc.)
      - Bold/italic wrappers (`**word**`, `*word*`, `__word__`, `_word_`)
      - Leading/trailing whitespace on each line
      - Consecutive blank lines (collapsed to single)

    This means the prompt's <spec_requirements> block can render the
    requirement without the spec's bullet formatting, but the meaningful
    content (e.g. "US-1: User can click ...") must match character-for-
    character after normalization.
    """
    if not text:
        return ""
    lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        # Strip leading list markers (-, *, +, 1., 1), a), etc.)
        line = re.sub(r"^\s*(?:[-*+]|\d+[\.\)]|[a-z]\))\s+", "", line)
        # Strip bold/italic wrappers: **X**, __X__, *X*, _X_
        line = re.sub(r"\*\*([^*]+)\*\*", r"\1", line)
        line = re.sub(r"__([^_]+)__", r"\1", line)
        line = re.sub(r"\*([^*]+)\*", r"\1", line)
        line = re.sub(r"_([^_]+)_", r"\1", line)
        # Normalize internal whitespace
        line = re.sub(r"\s+", " ", line).strip()
        lines.append(line)
    # Collapse consecutive blank lines
    out = []
    prev_blank = False
    for ln in lines:
        if not ln:
            if not prev_blank:
                out.append("")
            prev_blank = True
        else:
            out.append(ln)
            prev_blank = False
    return "\n".join(out)


def _extract_spec_block(prompt_text: str) -> str | None:
    """Extract content between <spec_requirements>...</spec_requirements>.
    Returns the normalized block content, or None if the block is missing.
    """
    match = re.search(
        r"<spec_requirements>(.*?)</spec_requirements>",
        prompt_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    return _normalize(match.group(1))


def _extract_invariants_block(prompt_text: str) -> str | None:
    """Extract content between <global_invariants>...</global_invariants>.
    Returns raw content (not normalized — caller decides), or None if the
    block is missing. An empty block returns the empty string, not None.
    """
    match = re.search(
        r"<global_invariants>(.*?)</global_invariants>",
        prompt_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1)


def _extract_mandatory_rules_block(prompt_text: str) -> str | None:
    """Extract content between <mandatory_rules>...</mandatory_rules>.
    Returns raw content (not normalized — caller decides), or None if the
    block is missing. An empty block returns the empty string, not None.
    """
    match = re.search(
        r"<mandatory_rules>(.*?)</mandatory_rules>",
        prompt_text,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1)


def _extract_file_change_map_files(spec_text: str) -> set[str]:
    """Extract every file path declared in the spec's `## File Change Map`
    section. Returns a set of normalized file paths (no backticks, no line
    refs, no leading slashes). Empty set if the section is missing or
    contains no parseable file rows.

    Recognizes two layouts:
      1. Markdown tables (most common — current forge template uses these)
         | File | What Changes | ... |
         | `models/user.go` | Add field | ... |
      2. Bullet lists (some specs use these instead of tables)
         - `models/user.go` — Add field [from A-NNN]

    File paths are normalized:
      - Stripped of surrounding backticks
      - Stripped of `:N` line refs (e.g. `foo.go:145` → `foo.go`)
      - Stripped of leading `./` or `/`
      - Trailing whitespace removed
    """
    if not spec_text:
        return set()
    section_match = re.search(
        r"^\s*##\s+File\s+Change\s+Map\s*\n(.*?)(?=^\s*##\s+|\Z)",
        spec_text,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not section_match:
        return set()
    section = section_match.group(1)
    files: set[str] = set()
    for raw_line in section.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Skip section sub-headings (### Modified Files / ### New Files)
        if line.startswith("#"):
            continue
        # Skip blockquote guidance lines
        if line.startswith(">"):
            continue
        # Table row — extract first cell
        if line.startswith("|"):
            # Skip separator rows like |---|---|
            inner = line.strip("|").replace(" ", "")
            if inner and set(inner) <= set("-:|"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not cells:
                continue
            first = cells[0]
            # Skip header rows: first cell is literally "File"
            if first.lower() in ("file", "path", "filename"):
                continue
            candidate = first
        # Bullet row — strip marker, take text up to first delimiter
        elif re.match(r"^[-*]\s+", line):
            content = re.sub(r"^[-*]\s+", "", line)
            # Take text up to first delimiter we recognize as "end of path"
            candidate = re.split(r"\s+(?:—|--|–|-|:|\(|\[)", content, 1)[0]
        else:
            continue
        # Normalize the candidate path
        path = _normalize_file_path(candidate)
        if path:
            files.add(path)
    return files


def _normalize_file_path(raw: str) -> str:
    """Strip backticks, line refs, leading ./, trailing whitespace.
    Returns empty string for non-paths (URLs, plain prose, etc.)."""
    s = raw.strip()
    # Strip backticks
    s = s.strip("`").strip()
    # Strip surrounding markdown link syntax [text](url) — keep the text
    link_match = re.match(r"^\[([^\]]+)\]\([^)]*\)$", s)
    if link_match:
        s = link_match.group(1).strip("`").strip()
    # Strip line refs: foo.go:145, foo.go:145-200
    s = re.sub(r":\d+(?:-\d+)?$", "", s)
    # Strip leading ./
    if s.startswith("./"):
        s = s[2:]
    # Strip leading / (treat as relative path)
    s = s.lstrip("/")
    # Reject obvious non-paths
    if not s or "/" not in s and "." not in s:
        return ""
    if " " in s:
        return ""
    if s.startswith(("http://", "https://")):
        return ""
    return s


def _extract_spec_invariants_section(spec_text: str) -> str:
    """Extract the `## Global Invariants` section from spec.md, if present.
    Returns the section body (everything until the next `## ` heading or
    end-of-file), stripped. Returns empty string if no such section exists.
    """
    if not spec_text:
        return ""
    match = re.search(
        r"^\s*##\s+Global\s+Invariants\s*\n(.*?)(?=^\s*##\s+|\Z)",
        spec_text,
        flags=re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    if not match:
        # Fallback: <global_invariants> block inline in the spec
        block = re.search(
            r"<global_invariants>(.*?)</global_invariants>",
            spec_text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        if block:
            return block.group(1).strip()
        return ""
    return match.group(1).strip()


def _find_drift(spec_block: str, normalized_spec: str) -> list[str]:
    """Return lines from the prompt's spec block that don't appear in
    normalized spec.md. The spec_block is already normalized when passed
    in (via _extract_spec_block → _normalize). We split the normalized
    spec by lines AND also check substring containment for multi-line
    cases.

    Short lines (<8 chars) are skipped to avoid false positives on
    things like '---' or 'EOF'.
    """
    drift: list[str] = []
    spec_lines = set(ln for ln in normalized_spec.splitlines() if ln.strip())
    for line in spec_block.splitlines():
        stripped = line.strip()
        if len(stripped) < 8:
            continue
        if stripped in spec_lines:
            continue
        # Fallback: substring match against the full normalized spec
        # (handles cases where the prompt wraps a requirement across
        # fewer or more lines than the spec does)
        if stripped in normalized_spec:
            continue
        drift.append(stripped)
    return drift


def _find_forbidden_phrases(prompt_text: str) -> list[str]:
    """Return any forbidden scope-cutting phrases found in the prompt."""
    lower = prompt_text.lower()
    found: list[str] = []
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in lower:
            found.append(phrase)
    return found
