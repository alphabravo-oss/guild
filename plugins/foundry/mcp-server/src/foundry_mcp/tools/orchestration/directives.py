"""Foundry-Tasks, Foundry-Directive, and the co-dispatch join.

Survey blocks DD and FF. One computation: which castings own the requirement
a defect or a directive names, and the block a lead pastes into each of them.
"""
from __future__ import annotations

from pathlib import Path

from foundry_mcp.schemas.vocab import (
    REQUIREMENT_ID_RE,
    is_requirement_id,
)
from foundry_mcp.tools.artifacts import (
    TASKS_GENERATED_MARKER,
    _artifact_guard,
    _load_json,
    _read_text,
)
from foundry_mcp.tools.concerns import open_cross_casting_concerns
from foundry_mcp.tools.foundry import ledger_refusals
from foundry_mcp.tools.foundry_state import (
    current_cycle,
    get_run_dir,
    now_iso,
    read_jsonl,
)
from pathlib import Path
from foundry_mcp.tools.orchestration.escalation import (
    _escalated_classes,
    _override_report,
    _record_escalation_proposals,
    _spend_structural_budget,
    _structural_proposal,
)




# --------------------------------------------------------------------------- #
# fallout FR-011 / FR-012 / FR-038 / GI-021 / GI-023 / CT-008 / CT-009 /
# ST-003 / AC-002 / AC-003 / AC-004 / AC-006 / OT-002 / OT-003 / OT-004 /
# OT-006 — A FIX REACHES EVERY SURFACE OF ITS RULE, IN THE SAME GRIND.
#
# A defect cites a requirement, the requirement is owned by more than one
# casting, and the fix lands in one of them. The other owners then carry the
# same rule spelled the old way until some later cycle files the same finding
# against them — which is a cycle spent re-discovering something the run already
# knew. The join is mechanical and the manifest already carries what it needs:
# `requirement_ids` per casting, persisted at F0.5 and validated at F0.9.
#
# SERVER-GENERATED, never lead-authored (GI-021). The alignment block is what a
# lead pastes VERBATIM into a dispatch prompt, so a lead composing it by hand is
# a lead deciding, per wave, how much of the join to carry across.
# --------------------------------------------------------------------------- #

#: fallout FR-048 / GI-017 / CT-010 / ST-011 — the handoff event `Foundry-Tasks`
#: writes per dispatched defect and `Foundry-Team-Down` reads. One event name,
#: declared where both ends can see it.
HANDOFF_EVENT_GRIND_DISPATCHED = "grind_dispatched"



#: The named refusal `Foundry-Team-Down` gives while a defect dispatched this
#: cycle is still open and a commit since the cycle baseline touched its file.
DISPATCHED_DEFECT_UNRECORDED = "DISPATCHED_DEFECT_UNRECORDED"




def _casting_requirement_ids(fdir: Path) -> tuple[dict[int, set[str]], bool]:
    """`{casting id: {requirement ids}}` from the manifest, and whether it CAN.

    The second element is False on a manifest whose castings carry no
    `requirement_ids` at all — a pre-FR-009 archive. That is reported as NOT
    COMPUTABLE and never as an empty set (AC-006 / OT-006): "no casting owns
    this requirement" and "nobody recorded who owns anything" are opposite
    facts, and a lead reading the first when the second is true dispatches one
    casting for a rule that lives in four.
    """
    manifest = _load_json(fdir / "castings" / "manifest.json")
    castings = manifest.get("castings")
    if not isinstance(castings, list):
        return {}, False
    owned: dict[int, set[str]] = {}
    any_declared = False
    for casting in castings:
        if not isinstance(casting, dict):
            continue
        raw = casting.get("requirement_ids")
        ids = {str(r) for r in raw if isinstance(r, str)} if isinstance(raw, list) else set()
        if raw is not None:
            any_declared = True
        try:
            cid = int(casting.get("id"))
        except (TypeError, ValueError):
            continue
        owned[cid] = ids
    return owned, any_declared




def _casting_files(fdir: Path) -> dict[int, list[str]]:
    """`{casting id: key_files}` — the sibling files an alignment block names."""
    manifest = _load_json(fdir / "castings" / "manifest.json")
    castings = manifest.get("castings")
    out: dict[int, list[str]] = {}
    if not isinstance(castings, list):
        return out
    for casting in castings:
        if not isinstance(casting, dict):
            continue
        try:
            cid = int(casting.get("id"))
        except (TypeError, ValueError):
            continue
        files = casting.get("key_files")
        out[cid] = [f for f in files if isinstance(f, str)] if isinstance(files, list) else []
    return out




def _owning_casting(fdir: Path, files: list[str]) -> int | None:
    """The casting whose key_files contain one of `files`, or None."""
    for cid, key_files in _casting_files(fdir).items():
        if any(f in key_files for f in files):
            return cid
    return None




def _co_dispatch_for(
    owned: dict[int, set[str]], requirement_ids: set[str], *, exclude: int | None = None
) -> list[int]:
    """Every casting whose `requirement_ids` intersect `requirement_ids`."""
    return sorted(
        cid for cid, ids in owned.items()
        if cid != exclude and ids & requirement_ids
    )




def _alignment_block(
    fdir: Path,
    *,
    defect_ids: list[str],
    requirement_ids: set[str],
    owning_casting: int | None,
    owning_files: list[str],
    co_dispatch: list[int],
) -> str:
    """The block a lead pastes VERBATIM into each co-dispatched prompt.

    Names the originating defects, the requirement ids, the owning casting and
    file of the fix, and each co-dispatched casting's sibling files that cite
    those ids. Rendered here rather than described to the lead, because a block
    the lead composes is a block that carries whatever the lead had room for.
    """
    files = _casting_files(fdir)
    lines = [
        "## Co-dispatch alignment (server-generated — paste verbatim)",
        "",
        f"Originating defect(s): {', '.join(defect_ids) or 'none'}",
        f"Requirement id(s): {', '.join(sorted(requirement_ids)) or 'none'}",
        (
            f"Fixed in casting {owning_casting}"
            + (f", file(s) {', '.join(owning_files)}" if owning_files else "")
            if owning_casting is not None
            else "Owning casting: not resolvable from the manifest"
        ),
        "",
        "The SAME requirement is owned by the castings below. Each one carries "
        "the rule on its own files; make the same change there, in that "
        "casting's own idiom, in THIS cycle.",
        "",
    ]
    for cid in co_dispatch:
        siblings = files.get(cid) or []
        lines.append(f"- casting {cid}: {', '.join(siblings) if siblings else 'no key_files recorded'}")
    return "\n".join(lines)




def _append_grind_dispatch(
    fdir: Path, *, defect_id: str, file_path: str, cycle: int, casting: int | None
) -> None:
    """Record that `defect_id` was dispatched into THIS GRIND cycle.

    fallout FR-022 / FR-048 / GI-017 / CT-010 / ST-011 / AC-039 / AC-041.

    Written by `Foundry-Tasks`, because that is the call that dispatches: it is
    what turns an open defect into a packet a teammate is handed. Read by
    `Foundry-Team-Down`, which refuses while one of these is still open and a
    commit since the cycle baseline touched its file — the shape of "the fix was
    made and nobody closed the ledger".

    Appended to `handoffs.jsonl` through casting 5's writer, so the dispatch
    record lives in the same file, the same format and the same order as every
    other handoff this run makes. Total: a run whose ledger cannot be written is
    a run with an unrecorded dispatch, which the door then cannot refuse on —
    the honest failure direction for an advisory record, and the same one every
    other ledger reader in this package takes.
    """
    from foundry_mcp.tools.foundry_handoff import _append_handoff_record

    entry = {
        "handoff_id": "",
        "timestamp": now_iso(),
        "event": HANDOFF_EVENT_GRIND_DISPATCHED,
        "defect_id": defect_id,
        "file": file_path,
        "cycle": cycle,
        "casting": casting,
    }
    try:
        _append_handoff_record(
            fdir,
            entry,
            [
                ("defect", f"`{defect_id}`"),
                ("file", f"`{file_path}`" if file_path else ""),
                ("cycle", f"`{cycle}`"),
                ("casting", f"`{casting}`" if casting is not None else ""),
            ],
        )
    except OSError:
        pass




def _grind_dispatches(fdir: Path, cycle: int) -> list[dict]:
    """Every `grind_dispatched` record this cycle wrote, oldest first."""
    records, problem = read_jsonl(fdir / "handoffs.jsonl")
    if problem is not None:
        return []
    return [
        r for r in records
        if isinstance(r, dict)
        and r.get("event") == HANDOFF_EVENT_GRIND_DISPATCHED
        and r.get("cycle") == cycle
    ]




def _dispatch_open_concerns(fdir: Path, tasks: list[dict]) -> list[str]:
    """Mark every open cross-casting concern the co-dispatch set reaches.

    fallout GI-023 / FR-012 / ST-003 / AC-004. A concern names a casting, a file
    or a symbol; when the set this call emits contains that casting — or the
    task's own files contain that file — the concern HAS been dispatched, and
    `inspect_start` stops refusing on it. Read and written through casting 1's
    `tools/concerns.py`, which owns the ledger; this supplies the join and
    nothing else.
    """
    from foundry_mcp.tools.concerns import mark_concerns_dispatched

    concerns = open_cross_casting_concerns(fdir)
    if not concerns:
        return []
    reached_castings: set[int] = set()
    reached_files: set[str] = set()
    for task in tasks:
        for cid in task.get("co_dispatch") or []:
            reached_castings.add(int(cid))
        for path in task.get("files") or []:
            reached_files.add(str(path))
    hit = [
        c["id"] for c in concerns
        if str(c.get("target", "")) in reached_files
        or str(c.get("target_casting_id", "")) in {str(cid) for cid in reached_castings}
    ]
    if not hit:
        return []
    mark_concerns_dispatched(fdir, hit)
    return sorted(hit)




# fallout GI-023 / D-127 — DECORATED, because this door now reaches a ledger.
#
# `Foundry-Tasks` marks the concerns its co-dispatch set carried, and that write
# goes through casting 1's `ledger_transaction`. A `LedgerShapeError` raised
# inside the locked primitive escapes across the MCP boundary as call_tool's
# unhandled-error banner rather than the house `{error, hint}` refusal — which
# is D-127 exactly, on a door that did not reach a ledger until this release.
# The pin that found it derives the reachable set from the source rather than
# from a list, which is why adding the reach and forgetting the decorator was a
# test failure and not a shipped defect.
@ledger_refusals
def foundry_defects_to_tasks(
    project_root: str = ".",
) -> dict:
    """Convert ALL open defects to grouped task descriptions for GRIND.

    FR-008 / ST-003 / AC-010. Defects of an ESCALATED class are lifted out of
    the location grouping and emitted as exactly ONE structural-fix packet per
    class, carrying a recorded proposal. Every other defect groups by location
    exactly as before — escalation changes the shape of one class's work and
    nothing else. An explicit ``escalation-override`` directive de-escalates a
    class, at which point its defects fall straight back into the per-instance
    grouping (AC-010).
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    data = _load_json(fdir / "defects.json")
    open_defects = [d for d in data.get("defects", []) if d.get("status") == "open"]

    escalated = _escalated_classes(fdir, project_root)
    # D-043: REGENERATED, never carried forward. This read
    # `info.get("proposal") or _structural_proposal(info)`, and `info["proposal"]`
    # is whatever `escalation.json` recorded on some earlier cycle — so the first
    # proposal a class ever drew was the one every later packet and every later
    # report carried, open counts and defect ids frozen at that moment. A class
    # whose three instances had since been FIXED still had the run's own final
    # report asserting it "still has 3 open instance(s)" and that all three
    # "must reach fixed". The string is cheap; the staleness was not.
    for key, info in escalated.items():
        info["proposal"] = _structural_proposal(info)
    # D-043: recorded BEFORE the nothing-to-do return below, not after. The
    # derived views on the escalation record — the proposal, `defect_ids`, the
    # LATENT backlog — are answers about the CURRENT ledger, and the run state
    # in which they most need refreshing is exactly the one this function used
    # to return from first: every instance fixed, nothing left to packet. The
    # record then kept the last thing said about the class while it still had
    # open work, and the F6 report printed it.
    _record_escalation_proposals(fdir, escalated)

    if not open_defects:
        return {"ok": True, "tasks": [], "count": 0, "escalated_classes": []}

    # ST-002 / FR-002 — THE STRUCTURAL-PASS BUDGET, SPENT HERE.
    #
    # One structural pass plus one retry, and then no more. The budget is
    # consumed by DISPATCH, so it is counted at the one place a structural
    # packet is ever emitted, and the class CLEARS on the crossing that spends
    # the last of it. Two passes is not a judgement that the class is fixed: it
    # is the statement that structural work has had its turn. Open LIVE
    # instances stay blocking defects and fall into the per-instance grouping
    # below on the very next call (AC-003); open LATENT instances go to the F6
    # named backlog (FR-001). Nothing is waived — only the SHAPE of the work
    # stops changing.
    #
    # D-058: this tool DISPATCHES and COUNTS. The exit itself is applied at the
    # `inspect_start` boundary that closes the cycle the budget-exhausting
    # packet was dispatched in — because ST-002's trigger is the second packet
    # CLOSING, and a packet dispatched by this call has not closed while this
    # call is still returning it. Clearing here retracted the class inside the
    # same call that emitted its packet, so a second `Foundry-Tasks` in the same
    # cycle — which a lead may make, and which is why `structural_packet_cycles`
    # exists — reported structural_tasks 0 for work still being done.
    packet_cycle = current_cycle(fdir)
    packets_counted = _spend_structural_budget(
        fdir, project_root, escalated, packet_cycle
    )

    escalated_ids = {did for info in escalated.values() for did in info["defect_ids"]}

    tasks = []

    # One packet per escalated class, emitted first so it leads the GRIND wave.
    for key in sorted(escalated):
        info = escalated[key]
        members = [d for d in open_defects if d["id"] in set(info["defect_ids"])]
        tasks.append({
            "structural": True,
            "defect_class": key,
            "class_declared": info["declared"],
            "consecutive_cycles": info["consecutive_cycles"],
            "cycles_seen": info["cycles"],
            "proposal": info["proposal"],
            "defect_ids": info["defect_ids"],
            "description": info["proposal"],
            "instances": [
                {"id": d["id"], "description": d.get("description", ""),
                 "file": d.get("file", ""), "symbol": d.get("symbol", "")}
                for d in members
            ],
            "files": info["files"],
            "symbols": info["symbols"],
            "spec_refs": info["spec_refs"],
            "regression": any(d.get("regression") for d in members),
            "source": info["sources"][0] if info["sources"] else "unknown",
        })

    MAX_PER_GROUP = 3
    groups: dict[str, list[dict]] = {}
    for d in open_defects:
        if d["id"] in escalated_ids:
            continue
        key = d.get("file") or d.get("symbol") or d["id"]
        groups.setdefault(key, []).append(d)

    for key, defects in groups.items():
        for i in range(0, len(defects), MAX_PER_GROUP):
            chunk = defects[i:i + MAX_PER_GROUP]
            task = {
                "structural": False,
                "defect_ids": [d["id"] for d in chunk],
                "description": "; ".join(d["description"] for d in chunk),
                "files": list({d["file"] for d in chunk if d.get("file")}),
                "symbols": list({d["symbol"] for d in chunk if d.get("symbol")}),
                "spec_refs": list({d["spec_ref"] for d in chunk if d.get("spec_ref")}),
                "regression": any(d.get("regression") for d in chunk),
                "source": chunk[0].get("source", "unknown"),
            }
            tasks.append(task)

    # fallout FR-011 / FR-038 / GI-021 / CT-008 / AC-002 / AC-006 / OT-002 /
    # OT-006 — THE CO-DISPATCH SET, PER TASK, AND THE BLOCK THAT CARRIES IT.
    owned, ids_declared = _casting_requirement_ids(fdir)
    open_by_id = {d["id"]: d for d in open_defects}
    for task in tasks:
        requirement_ids = {r for r in task.get("spec_refs", []) if r}
        if not ids_declared:
            # AC-006 / OT-006: NOT COMPUTABLE, never an empty set. A lead
            # reading "no other casting owns this" when the truth is "nobody
            # recorded who owns anything" dispatches one casting for a rule that
            # lives in four.
            task["co_dispatch"] = None
            task["co_dispatch_problem"] = (
                "castings/manifest.json declares no requirement_ids, so which "
                "castings own this defect's requirements is not computable. "
                "Re-run F0.5 DECOMPOSE, or accept that the fix reaches one "
                "casting only."
            )
            continue
        owning = _owning_casting(fdir, task.get("files") or [])
        co_dispatch = _co_dispatch_for(owned, requirement_ids, exclude=owning)
        task["co_dispatch"] = co_dispatch
        task["alignment_block"] = _alignment_block(
            fdir,
            defect_ids=list(task.get("defect_ids") or []),
            requirement_ids=requirement_ids,
            owning_casting=owning,
            owning_files=list(task.get("files") or []),
            co_dispatch=co_dispatch,
        )
        # fallout FR-048 / GI-017 / ST-011 — the dispatch RECORD, written by the
        # call that dispatches. `Foundry-Team-Down` reads these back and refuses
        # to tear a GRIND team down with one of them still open and a commit
        # since the cycle baseline touching its file.
        for did in task.get("defect_ids") or []:
            defect = open_by_id.get(did) or {}
            _append_grind_dispatch(
                fdir,
                defect_id=did,
                file_path=str(defect.get("file") or ""),
                cycle=packet_cycle,
                casting=owning,
            )

    # fallout GI-023 / FR-012 / ST-003 / AC-004 — a concern whose target is in
    # the co-dispatch set is DISPATCHED by this call, which is what lets
    # `inspect_start` stop refusing on it.
    dispatched_concerns = _dispatch_open_concerns(fdir, tasks)

    (fdir / TASKS_GENERATED_MARKER).write_text(f"{now_iso()} count={len(tasks)}\n", encoding="utf-8")

    result = {
        "ok": True,
        "tasks": tasks,
        "count": len(tasks),
        "escalated_classes": sorted(escalated),
        "structural_tasks": sum(1 for t in tasks if t["structural"]),
        "co_dispatch_computable": ids_declared,
        "concerns_dispatched": dispatched_concerns,
    }
    if packets_counted:
        # AC-004: what this call SPENT, reported where it happened. A lead that
        # dispatched the budget-exhausting packet needs to know it was the last
        # one this class will get — the exit itself lands at the next
        # `inspect_start`, when the packet has closed, and is reported there.
        result["structural_packets_counted"] = sorted(packets_counted)
    return result




# --- Directives (non-blocking human steering) ---


# --------------------------------------------------------------------------- #
# The directives.md marker grammar (D-104).
#
# ONE definition, read by both sides: `_read_directives` splits the file on
# these prefixes, and `foundry_inject_directive` refuses a body that contains
# one. Two hand-kept copies of the same grammar is how the forgery worked in
# the first place \u2014 the writer did not know what the reader would treat as
# structure, so a priority="normal" body carrying a line beginning
# `### [URGENT]` was parsed back out as a SECOND, urgent directive, overriding
# the priority argument. Directive text was trusted end to end; combined with
# D-101 that let any normal-priority prose forge urgency and de-escalate
# classes.
# --------------------------------------------------------------------------- #

_DIRECTIVE_HEADER_URGENT = "### [URGENT]"


_DIRECTIVE_HEADER_NORMAL = "### [DIRECTIVE]"


_DIRECTIVE_HEADERS = (_DIRECTIVE_HEADER_URGENT, _DIRECTIVE_HEADER_NORMAL)  # 2 markers



# The bootstrap text of an empty directives.md. ONE definition: the injector
# wrote it and the clearer wrote it back, as two separate string literals, so
# "is this file empty of directives?" could not be asked without re-typing a
# third copy — and D-129's conservation guard below has to ask exactly that.
_DIRECTIVES_PREAMBLE = (
    "# Foundry Directives\n\n"
    "Human steering inputs — read at every phase transition.\n\n"
)




def _directive_header_count(text: str) -> int:
    """How many priority headers the parser can see in ``text``."""
    return sum(
        1
        for line in text.split("\n")
        if any(line.startswith(h) for h in _DIRECTIVE_HEADERS)
    )




def _unaccounted_directive_text(path: Path, parsed: dict) -> str | None:
    """Content Foundry-Clear would destroy without archiving it, or None.

    D-129's second half, and the reason the guard alone is not enough. The
    encoding rung is closed upstream: an undecodable directives.md is now a
    NAMED refusal from ``_artifact_guard`` at every door. But the harm —
    "reports success, truncates the file, writes no archive record" — is
    reachable without any encoding fault at all. Hand-edit a ``###`` to a
    ``##`` and the parser sees no header, returns no directives, and the
    clearer truncates a file full of live human steering.

    So the destructive write carries its own conservation check: Foundry-Clear
    may only destroy what it could account for.

      * No header the parser recognises — the whole file is preamble. Truncating
        rewrites the preamble verbatim, so it is safe if that is genuinely all
        the file holds, and a refusal otherwise.
      * Headers present — every one of them must have produced a directive the
        archive will carry. A count that does not match means text is sitting
        in the file that the archive would not receive.

    Returns the unaccounted text (for the refusal to quote), or None when the
    file is fully accounted for.

    D-136 — CONSERVE CHARACTERS, NOT HEADERS. This compared the header COUNT to
    the parsed-directive count, which is blind to text the parser drops BEFORE
    the first recognised header: with a second, well-formed directive present
    the two counts reconcile and the check passes. Driven on the ordinary
    live-run case (one urgent + one normal, then the same `### [URGENT]` ->
    `## [URGENT]` hand-edit the shipped test itself exercises): Foundry-Clear
    returned ok with cleared_count=1, the urgent directive was GONE from
    directives.md, and directives-cleared.md recorded only the normal one. That
    is D-129's filed harm word for word -- "reports success, truncates the
    file, writes no archive record" -- surviving the fix meant to end it,
    because the conservation check was bound to the fixture the defect was
    reported on (ONE directive) instead of derived from what the file holds.
    A one-directive fixture cannot distinguish the two rules; the two-directive
    fixture is the regression test.

    The rule now subtracts, rather than counts. Everything the archive WILL
    carry is removed from the file's text once each -- the preamble, every line
    the parser reads as STRUCTURE, and every directive body it actually parsed.
    Whatever is still standing is text no archive record would carry, whatever
    else in the file parsed cleanly.
    """
    if not path.exists():
        return None
    text = _read_text(path)

    remainder = text
    if remainder.startswith(_DIRECTIVES_PREAMBLE):
        remainder = remainder[len(_DIRECTIVES_PREAMBLE):]

    # Structure, not content: a line the parser reads as a priority header is
    # consumed by the parse and is not part of any directive's body.
    remainder = "\n".join(
        line
        for line in remainder.split("\n")
        if not any(line.startswith(h) for h in _DIRECTIVE_HEADERS)
    )

    # Longest first, so a directive that is a SUBSTRING of another cannot
    # consume the other's text and leave a mangled remainder behind.
    bodies = sorted(
        (b for b in (*parsed.get("urgent", []), *parsed.get("normal", [])) if b),
        key=len,
        reverse=True,
    )
    for body in bodies:
        remainder = remainder.replace(body, "", 1)

    return remainder.strip() or None




def _forged_header_lines(directive: str) -> list[str]:
    """Body lines that `_read_directives` would parse as a priority header.

    Both the raw line and its left-stripped form are checked: the parser keys
    on `str.startswith`, so an indented marker is inert TODAY, but a body that
    smuggles one is asking for exactly the reading this refuses, and the cost
    of declining it is a rephrase.
    """
    return [
        line
        for line in directive.split("\n")
        if any(
            line.startswith(h) or line.lstrip().startswith(h)
            for h in _DIRECTIVE_HEADERS
        )
    ]




def foundry_inject_directive(
    directive: str,
    priority: str = "normal",
    project_root: str = ".",
) -> dict:
    """Inject a human directive that the lead reads at every phase transition."""
    fdir = get_run_dir(project_root)
    if not fdir or not fdir.exists():
        return {"error": "No active foundry run"}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt

    # D-104: refuse rather than escape. Escaping would silently alter the text
    # the human wrote, and a directive is a human instruction \u2014 the house
    # pattern for "this input cannot be honoured as given" is a named refusal
    # that quotes the offending value and says what to do instead.
    forged = _forged_header_lines(directive)
    if forged:
        return {
            "error": (
                "Directive body contains a line that would be read back as a "
                "priority header, which would split it into a second directive "
                "and override priority=" + repr(priority) + ": "
                + "; ".join(repr(line) for line in forged[:3])
                + ". Lines beginning "
                + " or ".join(repr(h) for h in _DIRECTIVE_HEADERS)
                + " are structure in directives.md, not content."
            ),
            "hint": (
                "Reword those lines \u2014 drop the leading '### ' or the square "
                "brackets. To file an urgent directive, pass priority='urgent'."
            ),
            "forged_header_lines": forged,
        }

    directives_path = fdir / "directives.md"
    if not directives_path.exists():
        directives_path.write_text(_DIRECTIVES_PREAMBLE, encoding="utf-8")

    with open(directives_path, "a", encoding="utf-8") as f:
        header = _DIRECTIVE_HEADER_URGENT if priority == "urgent" else _DIRECTIVE_HEADER_NORMAL
        f.write(f"\n{header} {now_iso()}\n\n{directive}\n")

    result = {
        "ok": True,
        "priority": priority,
        "message": "Directive injected \u2014 lead will read it at next phase transition",
    }

    # fallout FR-011 / GI-021 / CT-009 / AC-003 / OT-003 — THE SAME JOIN THE
    # TASKS DOOR MAKES, ON THE SAME TABLE.
    #
    # A human directive that names a requirement id names a rule, and a rule is
    # owned by however many castings the manifest says it is. The ids are found
    # with `vocab.is_requirement_id` — the ONE grammar, never a second regex
    # here — and the union of their owners is printed. A directive naming NO id
    # prints an empty set and is otherwise unchanged, which is the ordinary case
    # and must stay free: most directives are instructions, not citations.
    owned, ids_declared = _casting_requirement_ids(fdir)
    directive_ids = {
        token for token in REQUIREMENT_ID_RE.findall(directive)
        if is_requirement_id(token)
    }
    result["requirement_ids"] = sorted(directive_ids)
    if not ids_declared:
        result["co_dispatch"] = None
        result["co_dispatch_problem"] = (
            "castings/manifest.json declares no requirement_ids, so which "
            "castings own the ids in this directive is not computable."
        )
    else:
        result["co_dispatch"] = _co_dispatch_for(owned, directive_ids)

    # D-133: report the override decision HERE, at the call that made it. The
    # operator learns immediately whether the marker they just sent was read,
    # matched nothing, or was ignored as quoted \u2014 instead of discovering it by
    # watching what the next Foundry-Tasks emits.
    override = _override_report(fdir, project_root)
    if override["decisions"]:
        result["escalation_override"] = override
        result["message"] += " | escalation-override: " + "; ".join(
            override["decisions"]
        )
    return result




DIRECTIVES_CLEARED_FILENAME = "directives-cleared.md"




def foundry_clear_directives(
    project_root: str = ".",
) -> dict:
    """Clear active directives, preserving a record of what was cleared.

    FR-019. This used to truncate directives.md outright, leaving no record of
    what the human had asked for or whether it was ever honoured \u2014 the run's
    steering history was destroyed by the act of acknowledging it. The cleared
    text is now appended to ``directives-cleared.md`` first, so the audit trail
    survives and a later reader can check a directive against the work.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run."}
    if (corrupt := _artifact_guard(fdir)):
        return corrupt
    directives_path = fdir / "directives.md"

    active = _read_directives(project_root)
    urgent = active.get("urgent", [])
    normal = active.get("normal", [])
    cleared_count = len(urgent) + len(normal)

    # D-129: refuse rather than destroy. FR-019 makes preserving a record this
    # tool's whole contract, and a truncate that outruns the archive breaks it
    # silently — the operator is told "No active directives to clear" while the
    # directives are being deleted.
    if (unaccounted := _unaccounted_directive_text(directives_path, active)) is not None:
        return {
            "error": (
                f"directives.md holds {len(unaccounted)} characters of text that "
                f"this tool could not parse as directives, so clearing it would "
                f"DESTROY content no archive record would carry. Refused. "
                f"Unparsed text begins: {unaccounted[:160]!r}"
            ),
            "hint": (
                "The file's header grammar is broken — a directive block opens "
                + " or ".join(repr(h) for h in _DIRECTIVE_HEADERS)
                + " at the start of a line. Repair the headers (or move the text "
                "somewhere safe and reset the file) and retry. Nothing was "
                "cleared and nothing was written."
            ),
            "unaccounted_characters": len(unaccounted),
            "cleared_count": 0,
        }

    if cleared_count:
        archive = fdir / DIRECTIVES_CLEARED_FILENAME
        if not archive.exists():
            archive.write_text(
                "# Cleared Directives\n\nEvery directive Foundry-Clear has retired, "
                "with the time it was cleared. Nothing here is deleted.\n",
                encoding="utf-8",
            )
        with open(archive, "a", encoding="utf-8") as f:
            f.write(f"\n## Cleared {now_iso()}\n\n")
            for text in urgent:
                f.write(f"- **[URGENT]** {text}\n")
            for text in normal:
                f.write(f"- **[DIRECTIVE]** {text}\n")

    if directives_path.exists():
        directives_path.write_text(_DIRECTIVES_PREAMBLE, encoding="utf-8")

    return {
        "ok": True,
        "cleared_count": cleared_count,
        "urgent_cleared": len(urgent),
        "normal_cleared": len(normal),
        "record": str(fdir / DIRECTIVES_CLEARED_FILENAME) if cleared_count else "",
        "message": (
            f"{cleared_count} directive(s) cleared \u2014 recorded in "
            f"{DIRECTIVES_CLEARED_FILENAME}"
            if cleared_count
            else "No active directives to clear"
        ),
    }




def _read_directives(project_root: str) -> dict:
    """Read active directives."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"has_directives": False, "urgent": [], "normal": [], "raw_text": ""}
    directives_path = fdir / "directives.md"

    if not directives_path.exists():
        return {"has_directives": False, "urgent": [], "normal": [], "raw_text": ""}

    # D-098: a non-UTF-8 byte in directives.md raised UnicodeDecodeError out of
    # here and therefore out of Foundry-Next, the mandatory handshake.
    text = _read_text(directives_path)

    urgent: list[str] = []
    normal: list[str] = []
    current_priority = None
    current_text: list[str] = []

    for line in text.split("\n"):
        if line.startswith(_DIRECTIVE_HEADER_URGENT):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "urgent"
            current_text = []
        elif line.startswith(_DIRECTIVE_HEADER_NORMAL):
            if current_priority and current_text:
                target = urgent if current_priority == "urgent" else normal
                target.append("\n".join(current_text).strip())
            current_priority = "normal"
            current_text = []
        elif current_priority:
            current_text.append(line)

    if current_priority and current_text:
        target = urgent if current_priority == "urgent" else normal
        target.append("\n".join(current_text).strip())

    has = len(urgent) > 0 or len(normal) > 0
    return {"has_directives": has, "urgent": urgent, "normal": normal, "raw_text": text if has else ""}
