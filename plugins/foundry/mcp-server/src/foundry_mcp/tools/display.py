"""Rich display formatting for MCP tool results.

Transforms raw dicts into visually appealing terminal output with
ANSI colors and pixel-art hammer branding for foundry tools.

Falls back to JSON for unknown tool names.
"""

from __future__ import annotations

import json
import os

from foundry_mcp.schemas.vocab import STREAM_WIRE_IDS


def _short_path(p: str) -> str:
    """Shorten an absolute path to be relative to cwd or home."""
    if not p or p == "?":
        return p
    cwd = os.getcwd()
    try:
        rel = os.path.relpath(p, cwd)
        if len(rel) < len(p):
            return rel
    except ValueError:
        pass
    home = os.path.expanduser("~")
    if p.startswith(home):
        return "~" + p[len(home):]
    return p


# ── ANSI colors ──────────────────────────────────────────────────────────────

_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"

_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"

_BG_RED = "\033[41m"
_BG_GREEN = "\033[42m"
_BG_YELLOW = "\033[43m"
_BG_BLUE = "\033[44m"
_BG_CYAN = "\033[46m"

_BRED = f"{_BOLD}{_RED}"
_BGREEN = f"{_BOLD}{_GREEN}"
_BYELLOW = f"{_BOLD}{_YELLOW}"
_BCYAN = f"{_BOLD}{_CYAN}"
_BWHITE = f"{_BOLD}{_WHITE}"
_BMAGENTA = f"{_BOLD}{_MAGENTA}"


# ── Box drawing helpers ──────────────────────────────────────────────────────

_W = 60  # default box width (inner)

_PHASE_NAMES = {
    "F0": "RESEARCH", "F0.5": "DECOMPOSE", "F0.9": "VALIDATE",
    "F1": "CAST", "F2": "INSPECT", "F3": "GRIND", "F4": "ASSAY",
    "F5": "TEMPER", "F5.5": "NYQUIST", "F6": "DONE",
}


def _box(title: str, lines: list[str], width: int = _W, color: str = _BCYAN) -> str:
    """Draw a colored box with a title bar and content lines."""
    top = f"{color}\u2554{'\u2550' * (width + 2)}\u2557{_RESET}"
    title_line = f"{color}\u2551{_RESET} {_BWHITE}{title:<{width}}{_RESET} {color}\u2551{_RESET}"
    sep = f"{color}\u2560{'\u2550' * (width + 2)}\u2563{_RESET}"
    bottom = f"{color}\u255a{'\u2550' * (width + 2)}\u255d{_RESET}"
    body = [f"{color}\u2551{_RESET} {line:<{width}} {color}\u2551{_RESET}" for line in lines]
    return "\n".join([top, title_line, sep, *body, bottom])


def _mini_box(title: str, lines: list[str], width: int = 50, color: str = _BCYAN) -> str:
    """Compact colored box for quick status results."""
    top = f"{color}\u250c{'\u2500' * (width + 2)}\u2510{_RESET}"
    title_line = f"{color}\u2502{_RESET} {_BWHITE}{title:<{width}}{_RESET} {color}\u2502{_RESET}"
    sep = f"{color}\u251c{'\u2500' * (width + 2)}\u2524{_RESET}"
    bottom = f"{color}\u2514{'\u2500' * (width + 2)}\u2518{_RESET}"
    body = [f"{color}\u2502{_RESET} {line:<{width}} {color}\u2502{_RESET}" for line in lines]
    return "\n".join([top, title_line, sep, *body, bottom])


def _bar(value: int, total: int, width: int = 30, fill: str = "\u2588", empty: str = "\u2591") -> str:
    """Render a colored progress bar."""
    if total == 0:
        return f"{_DIM}{empty * width}{_RESET}  0/0"
    filled = int(value / total * width) if total > 0 else 0
    pct = int(value / total * 100) if total > 0 else 0
    # Color based on percentage
    if pct >= 95:
        bar_color = _BGREEN
    elif pct >= 50:
        bar_color = _BYELLOW
    else:
        bar_color = _BRED
    return f"{bar_color}{fill * filled}{_DIM}{empty * (width - filled)}{_RESET}  {value}/{total} ({pct}%)"


def _pass_fail(passed: bool) -> str:
    """Render PASS or FAIL with color."""
    if passed:
        return f"{_BGREEN}PASS{_RESET}"
    return f"{_BRED}FAIL{_RESET}"


def _status_icon(ok: bool) -> str:
    """Render a check or cross."""
    if ok:
        return f"{_GREEN}\u2713{_RESET}"
    return f"{_RED}\u2717{_RESET}"


# ── Foundry hammer header ────────────────────────────────────────────────────

FOUNDRY_SEP = f"{_DIM}{'\u2500' * 44}{_RESET}"


def foundry_hammer(label: str) -> str:
    """Render the foundry pixel-art hammer header with a label.

    Public API — used by display.py formatters and server-side foundry tools.
    """
    return "\n".join([
        f"{_BCYAN}   \u2584\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2584{_RESET}",
        f"{_BCYAN}   \u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588\u2588{_RESET}",
        f"{_BCYAN}   \u2580\u2580\u2580\u2580\u2588\u2588\u2580\u2580\u2580\u2580{_RESET}",
        f"{_BCYAN}       \u2588\u2588{_RESET}     {_BWHITE}{label}{_RESET}",
        f"{_BCYAN}       \u2588\u2588{_RESET}",
    ])


def _foundry_display(label: str, lines: list[str]) -> str:
    """Render a foundry tool result with hammer header, body lines, and separator."""
    parts = [foundry_hammer(label)]
    for line in lines:
        parts.append(line)
    parts.append(FOUNDRY_SEP)
    return "\n".join(parts)


# ── Per-tool formatters ──────────────────────────────────────────────────────


def _fmt_validate_report(r: dict) -> str:
    valid = r.get("valid", False)
    errors = r.get("errors", [])
    stats = r.get("stats", {})

    lines = [f"  Result:  {_pass_fail(valid)}"]
    if errors:
        lines.append(f"  {_RED}Errors:  {len(errors)}{_RESET}")
        for e in errors[:5]:
            lines.append(f"    {_RED}\u2022{_RESET} {e}")
        if len(errors) > 5:
            lines.append(f"    {_DIM}... +{len(errors) - 5} more{_RESET}")
    if stats:
        lines.append("")
        for k, v in stats.items():
            if isinstance(v, dict):
                lines.append(f"  {k}:")
                for sk, sv in v.items():
                    lines.append(f"    {sk:<16} {sv}")
            else:
                lines.append(f"  {k + ':':<18} {v}")
    return _box("Report Validation", lines)


def _fmt_verify_citations(r: dict) -> str:
    passed = r.get("pass", False)
    summary = r.get("summary", {})

    lines = [f"  Result:  {_pass_fail(passed)}"]
    if summary:
        lines.append("")
        lines.append(f"  Requirements:  {summary.get('total_requirements', 0)}")
        lines.append(f"  Covered:       {summary.get('covered_requirements', 0)}")
        lines.append(f"  Uncovered:     {summary.get('uncovered_requirements', 0)}")
        lines.append(f"  Coverage:      {summary.get('coverage_pct', 'N/A')}")
        lines.append("")
        lines.append(f"  Verdicts:      {summary.get('total_verdicts', 0)}")
        lines.append(f"  Verified:      {summary.get('verified_verdicts', 0)}")
        lines.append(f"  Non-verified:  {summary.get('non_verified_verdicts', 0)}")
        lines.append(f"  Orphan:        {summary.get('orphan_verdicts', 0)}")
    issues = summary.get("issues", [])
    if issues:
        lines.append("")
        lines.append("  Issues:")
        for issue in issues[:5]:
            lines.append(f"    {_YELLOW}\u2022{_RESET} {issue}")
    return _box("Citation Verification", lines)


# ── Foundry formatters ────────────────────────────────────────────────────────


def _executing_build_lines(r: dict) -> list[str]:
    """AC-027 / OT-018 / FR-017 — WHICH BUILD IS EXECUTING THIS RUN.

    A plugin-targeting run whose executing server is a stale cached copy cannot
    use the process fixes it is itself shipping, and nothing on screen said so.
    These four facts are written by `foundry_init` at F0 and merely rendered
    here; a missing key omits its line rather than raising, which is this
    module's rule for every optional fact (see the named-refusal block).

    D-041 — WHY THIS IS A FUNCTION AND WHY BOTH CALLERS APPEND IT.
    -------------------------------------------------------------
    These lines lived inline in `_fmt_foundry_init`, below an
    ``if "display" in r: return r["display"]`` early return — and `foundry_init`
    sets `display` on EVERY success, so on the success path they were
    unreachable. On the refusal path they were unreachable for a second,
    independent reason: `foundry_init`'s self-target refusal carries no
    `display`, so the block did render, but it rendered no `error` text, and
    `format_result` therefore threw the whole rendering away for
    `_house_refusal_display`. Code with no reachable caller, carrying the one
    fact the run exists to make visible.

    So the block is a helper both paths APPEND, rather than a tail both paths
    have to fall through to. Same shape `_fmt_foundry_next_lines` uses against
    the Foundry-Next result: the pre-rendered box is built by a different
    module (`foundry.py#_format_init_display`, which renders none of these) and
    is concatenated with, never returned instead of.
    """
    lines: list[str] = []
    for label, key in (
        ("Server", "server_version"),
        ("Plugin", "plugin_version"),
        ("Root", "server_root"),
        ("Commit", "server_commit"),
    ):
        value = r.get(key)
        if not value:
            continue
        shown = _short_path(str(value)) if key == "server_root" else str(value)
        lines.append(f"  {_BWHITE}{label}:{_RESET}{' ' * max(1, 8 - len(label))}{shown}")
    if r.get("self_target"):
        lines.append(
            f"  {_BWHITE}Target:{_RESET} {_BYELLOW}self{_RESET} "
            f"{_DIM}(this run builds the plugin it is executing on){_RESET}"
        )
    return lines


def _fmt_foundry_init(r: dict) -> str:
    build = _executing_build_lines(r)

    # D-011 / D-041 / ST-009 / CT-010 — THE REFUSAL PATH RENDERS ITS OWN
    # REFUSAL, OR THE HOUSE NET TAKES THE WHOLE RENDERING AWAY.
    #
    # `format_result` discards a formatter's output when the result named a
    # refusal the rendering does not contain. This formatter had no error
    # branch at all, so a self-target refusal — the one refusal whose entire
    # value is four version facts and a command to relaunch with — fell through
    # to `_house_refusal_display`, which knows only error / corrupt_artifacts /
    # hint. Driven: `foundry_init` against a 9.9.9 tree rendered no
    # `claude --plugin-dir` substring anywhere, while its own hint said
    # "relaunch with the command above".
    refusal = _named_refusal(r)
    if refusal is not None:
        lines = [f"  {_RED}{refusal}{_RESET}"]
        if build:
            lines.append("")
            lines.extend(build)
        lines.extend(_launch_command_lines(r))
        hint = r.get("hint")
        if isinstance(hint, str) and hint.strip():
            lines.append("")
            lines.append(f"  {_DIM}{hint}{_RESET}")
        return _foundry_display(f"F O U N D R Y  {_BRED}Init refused{_RESET}", lines)

    if "display" in r:
        pre_rendered = r["display"]
        if build:
            return pre_rendered + "\n" + "\n".join(build)
        return pre_rendered
    lines = [
        f"  {_BWHITE}Dir:{_RESET}    {_short_path(r.get('foundry_dir', '?'))}",
        f"  {_BWHITE}Name:{_RESET}   {r.get('run_name', '?')}",
        f"  {_BWHITE}Files:{_RESET}  {', '.join(r.get('files_created', []))}",
        f"  {_BWHITE}Spec:{_RESET}   {'copied' if r.get('spec_copied') else 'none'}",
    ]
    lines.extend(build)
    return _foundry_display("F O U N D R Y  Initialized", lines)


def _retier_line(r: dict) -> str:
    """The `re-tiered <id> <TIER>` line both filing doors' formatters render.

    D-100 — THE RE-TIER OUTCOME NEVER CROSSED THE MCP BOUNDARY.
    ----------------------------------------------------------
    Both doors return `retiered` and `retiered_ids`, under the same two key
    names and with a comment saying so "so a lead or a report reading either
    door's result handles one shape" — and `format_result` dropped both, on both
    doors. Driven with ANSI stripped: `foundry_add_defect` against a ledger
    holding an open untiered D-001 returned retiered=1, retiered_ids=['D-001']
    and rendered "F O U N D R Y  Defect: D-001 / Total: 1  Open: 1"; the
    identical call against an EMPTY ledger returned retiered=0, retiered_ids=[]
    and rendered the BYTE-IDENTICAL two lines. `foundry_sync_defects` returning
    retiered=1 rendered "Added: +0 / Reopened: 0 / Total open: 1", which reads
    as "the batch recorded nothing" for a batch that reclassified an open
    blocking record in place.

    That matters because `_blocking_defects`' hint instructs the lead to re-file
    each untiered defect through either door PRECISELY so the blocking count
    moves. The screen never said it did, so the return trip on the documented
    recovery path failed and the lead's rational next move was to re-file again
    or conclude the exit does not work.

    ONE renderer for both doors, for the same reason the two doors report under
    one pair of key names: a second spelling of one event is how the two
    surfaces come to disagree about it. The heading is the one the forge-log.md
    mirror already computes — "<id> re-tiered <TIER>" — so the terminal and the
    human log say the same thing about the same event. The tier is rendered when
    the result carries one and omitted when it does not, rather than re-read
    from the ledger: this module renders result dicts and reads no run artifact.
    """
    ids = r.get("retiered_ids")
    if not isinstance(ids, list) or not ids:
        return ""
    tier = r.get("tier")
    suffix = f" {tier}" if isinstance(tier, str) and tier.strip() else ""
    named = ", ".join(str(i) for i in ids)
    return (
        f"  {_BGREEN}re-tiered{_RESET} {_BYELLOW}{named}{_RESET}{suffix} "
        f"{_DIM}(classified in place — the record keeps its id){_RESET}"
    )


def _fmt_foundry_add_defect(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    defect_id = r.get("defect_id", "?")
    total = r.get("total_defects", 0)
    open_count = r.get("open_defects", 0)
    lines = [f"  Total: {total}  Open: {_BYELLOW}{open_count}{_RESET}"]
    # D-100: a re-tier and a fresh append rendered identically, so the lead
    # could not tell which of the two had just happened.
    if (retier := _retier_line(r)):
        lines.append(retier)
    return _foundry_display(
        f"F O U N D R Y  Defect: {_BYELLOW}{defect_id}{_RESET}", lines
    )


def _fmt_foundry_query_defects(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    summary = r.get("summary", {})
    defects = r.get("defects", [])
    total = summary.get("total", 0)
    open_count = summary.get("open", 0)
    fixed = summary.get("fixed", 0)

    lines = [
        f"  Total: {_BWHITE}{total}{_RESET}  "
        f"Open: {_BYELLOW}{open_count}{_RESET}  "
        f"Fixed: {_BGREEN}{fixed}{_RESET}",
    ]

    by_source = summary.get("by_source", {})
    if by_source:
        lines.append("")
        lines.append(f"  {_BWHITE}By Source{_RESET}")
        for src, count in sorted(by_source.items()):
            lines.append(f"    {src:<12} {count}")

    by_type = summary.get("by_type", {})
    if by_type:
        lines.append("")
        lines.append(f"  {_BWHITE}By Type{_RESET}")
        for typ, count in sorted(by_type.items()):
            lines.append(f"    {typ:<12} {count}")

    if defects:
        lines.append("")
        lines.append(f"  {_BWHITE}{'ID':<8} {'SRC':<8} {'TYPE':<10} {'STATUS':<8} DESCRIPTION{_RESET}")
        lines.append(f"  {_DIM}{'\u2500' * 8} {'\u2500' * 8} {'\u2500' * 10} {'\u2500' * 8} {'\u2500' * 20}{_RESET}")
        for d in defects[:15]:
            desc = d.get("description", "")[:35]
            status = d.get("status", "?")
            status_color = _GREEN if status == "fixed" else _YELLOW
            lines.append(
                f"  {d.get('id', '?'):<8} {d.get('source', '?'):<8} "
                f"{d.get('type', '?'):<10} {status_color}{status:<8}{_RESET} {desc}"
            )
        if len(defects) > 15:
            lines.append(f"  {_DIM}... +{len(defects) - 15} more{_RESET}")

    return _foundry_display("F O U N D R Y  Defect Ledger", lines)


def _fmt_foundry_add_verdict(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    req = r.get("requirement_id", "?")
    verdict = r.get("verdict", "?")
    verified = r.get("verified_count", 0)
    total = r.get("total_requirements", 0)

    if verdict == "VERIFIED":
        v_color = _BGREEN
    elif verdict in ("THIN", "PARTIAL"):
        v_color = _BYELLOW
    else:
        v_color = _BRED

    replaced = f" {_DIM}(replaced){_RESET}" if r.get("replaced_existing") else ""

    return _foundry_display(f"F O U N D R Y  Verdict: {req}", [
        f"  {v_color}{verdict}{_RESET}{replaced}",
        f"  Progress: {_bar(verified, total, width=20)}",
    ])


def _fmt_foundry_verify_coverage(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    cs = r.get("coverage_summary", {})
    ds = r.get("defect_summary", {})
    gaps = r.get("gaps", [])
    passed = r.get("pass", False)

    total = cs.get("total_requirements", 0)
    verified = cs.get("verified", 0)

    lines = [
        f"  Result:    {_pass_fail(passed)}",
        f"  Coverage:  {_bar(verified, total)}",
        "",
        f"  Requirements: {total}  Verified: {_BGREEN}{verified}{_RESET}  "
        f"Non-verified: {cs.get('non_verified', 0)}  Uncovered: {cs.get('uncovered', 0)}",
        f"  Defects:      {ds.get('total', 0)}  Open: {ds.get('open', 0)}  Fixed: {ds.get('fixed', 0)}",
    ]

    if gaps:
        lines.append("")
        lines.append(f"  {_BWHITE}Gaps:{_RESET}")
        for g in gaps[:10]:
            lines.append(
                f"    {g.get('requirement_id', '?'):<10} "
                f"{_YELLOW}{g.get('status', '?'):<14}{_RESET} "
                f"defects: {g.get('open_defect_count', 0)}"
            )
        if len(gaps) > 10:
            lines.append(f"    {_DIM}... +{len(gaps) - 10} more{_RESET}")

    return _foundry_display("F O U N D R Y  Coverage Traceability", lines)


def _fmt_foundry_gate(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    passed = r.get("passed", False)
    phase = r.get("phase", "?")
    phase_name = _PHASE_NAMES.get(phase.upper(), phase)

    # Hide failed gate checks — the lead retries automatically, no need to surface
    if not passed:
        reason = r.get("reason", "")
        return f"{_DIM}Gate {phase_name}: not ready \u2014 {reason}{_RESET}"

    lines = [f"  {_pass_fail(passed)}"]

    checklist = r.get("checklist", [])
    if checklist:
        lines.append("")
        for item in checklist:
            check = item.get("check", "?")
            lines.append(f"    [{_status_icon(item.get('ok'))}] {check}")

    return _foundry_display(f"F O U N D R Y  Gate: {phase_name}", lines)


def _fmt_foundry_mark_phase_complete(r: dict) -> str:
    if r.get("error"):
        # Hide blocked transitions — lead retries automatically
        reason = r.get("error", "")
        return f"{_DIM}Phase transition blocked: {reason}{_RESET}"
    phase = r.get("phase", "?")
    phase_name = _PHASE_NAMES.get(phase, phase)
    return _foundry_display(f"F O U N D R Y  \u2192 {phase} {phase_name}", [
        f"  {r.get('message', '')}",
    ])


def _fmt_foundry_next_lines(r: dict) -> list[str]:
    """The per-fact lines Foundry-Next gained: width, spend, unreported, halt.

    One labelled line per fact and never a raise — a key that is not there
    contributes no line. That is this module's rule and not a stylistic one: a
    formatter that indexes an optional key is how D-157 put a literal '?' on
    screen while the handler had returned a fully-worded refusal.

    Everything here is REPORTED (GI-008 / GI-009). The width and its rule were
    decided by the transition that opened the INSPECT and recorded in
    state.json; the spend totals are what the lead typed into Foundry-Spend. No
    line below computes a value, and none of them contains a money figure — this
    server does not know anyone's rate card (AC-033).
    """
    lines: list[str] = []

    mode = r.get("inspect_mode")
    if isinstance(mode, dict) and mode.get("mode"):
        colour = _BYELLOW if mode["mode"] == "FULL" else _BGREEN
        lines.append(
            f"  {_BWHITE}Inspect:{_RESET}  {colour}{mode['mode']}{_RESET} "
            f"{_DIM}(rule {mode.get('rule', '?')}, decided at "
            f"{mode.get('decided_by', '?')}){_RESET}"
        )
        required = mode.get("required_streams") or []
        if required:
            lines.append(f"  {_BWHITE}Roster:{_RESET}   {', '.join(required)}")
        scope = mode.get("stream_scope") or {}
        skipped = sorted(
            wire for wire, v in scope.items()
            if isinstance(v, dict) and v.get("scope") == "skipped"
        )
        if skipped:
            lines.append(f"  {_BWHITE}Skipped:{_RESET}  {_DIM}{', '.join(skipped)}{_RESET}")
        sample = mode.get("prove_sample") or []
        if sample:
            shown = ", ".join(sample[:8]) + ("..." if len(sample) > 8 else "")
            lines.append(f"  {_BWHITE}PROVE:{_RESET}    {len(sample)} row(s) — {shown}")

    spend = r.get("spend")
    if isinstance(spend, dict):
        total = spend.get("total") or {}
        if total.get("agents") or spend.get("unreported_count"):
            minutes = int(total.get("duration_ms", 0) // 60000)
            lines.append(
                f"  {_BWHITE}Spend:{_RESET}    {total.get('tokens', 0):,} tokens  "
                f"{minutes}m  over {total.get('agents', 0)} reported agent(s)"
            )
        for label, section in (("by phase", "by_phase"), ("by cycle", "by_cycle")):
            buckets = spend.get(section) or {}
            if not isinstance(buckets, dict) or not buckets:
                continue
            parts = [
                f"{key}: {b.get('tokens', 0):,}tok/{int(b.get('duration_ms', 0) // 60000)}m"
                for key, b in sorted(buckets.items())
                if isinstance(b, dict)
            ]
            if parts:
                lines.append(f"  {_BWHITE}{label.title()}:{_RESET} {_DIM}{'  '.join(parts)}{_RESET}")
        unreported = spend.get("unreported_dispatches") or []
        if unreported:
            names = ", ".join(
                f"{u.get('agent', '?')}@{u.get('phase', '?')}" for u in unreported[:6]
            )
            more = f" (+{len(unreported) - 6} more)" if len(unreported) > 6 else ""
            lines.append(
                f"  {_BWHITE}Unreported:{_RESET} {_BYELLOW}{len(unreported)}{_RESET} "
                f"{_DIM}{names}{more} — no gate blocks on these{_RESET}"
            )

    build = r.get("executing_server")
    if isinstance(build, dict) and (build.get("server_version") or build.get("server_commit")):
        commit = str(build.get("server_commit", "") or "")
        lines.append(
            f"  {_BWHITE}Server:{_RESET}   {build.get('server_version', '?')} "
            f"{_DIM}(plugin {build.get('plugin_version', '?')} @ "
            f"{commit[:12] or 'unknown'}, {_short_path(str(build.get('server_root', '?')))})"
            f"{_RESET}"
        )

    waiting = r.get("waiting_on_agents")
    if isinstance(waiting, dict) and waiting.get("waiting"):
        lines.append(
            f"  {_BWHITE}Waiting:{_RESET}  {waiting.get('count', 0)} agent(s) "
            f"{_DIM}({waiting.get('detail', '')}){_RESET}"
        )

    if r.get("phase") == "HALTED":
        lines.append(
            f"  {_BRED}HALTED:{_RESET}   "
            f"{(r.get('details') or {}).get('halted_reason', 'cycle cap reached')}"
        )
    return lines


def _fmt_foundry_next_action(r: dict) -> str:
    # Always show the pixel-art status header (from the `display` field), THEN
    # the per-fact lines, THEN the imperative instructions (which lead with a
    # "YOUR NEXT CALL:" line from Phase 6).
    #
    # D-018 — THE PRE-RENDERED BLOCK WAS RETURNED *INSTEAD OF* THESE LINES.
    #
    # `foundry_next_action` sets `display` on every call, so both early returns
    # below fired every time and `_fmt_foundry_next_lines` was never reached in
    # production: the lead saw the run's spend TOTAL and nothing else, while
    # per-phase and per-cycle roll-ups — the two numbers FR-021 exists to
    # deliver — were computed, put in the result dict, and thrown away at the
    # renderer. The second symptom was worse than the omission: the unreachable
    # renderer held a second copy of the Inspect / Spend / Server lines that
    # `_format_status_display` also drew, and the two had already drifted (the
    # dead copy named `server_root`, the live one did not).
    #
    # Concatenated, never substituted. That is the same repair `_fmt_foundry_
    # init` makes against the same cause, and it is what lets
    # `_format_status_display` drop its copy of these four groups: there is now
    # exactly ONE renderer for each of them, and it is this one.
    instructions = r.get("instructions", "")
    pre_rendered = r.get("display")
    facts = _fmt_foundry_next_lines(r)
    if pre_rendered:
        block = pre_rendered
        if facts:
            block = block + "\n" + "\n".join(facts)
        if instructions:
            return f"{block}\n\n{instructions}"
        return block
    return _foundry_display(f"F O U N D R Y  {r.get('phase', '?')}", [
        f"  {_BWHITE}Action:{_RESET}  {r.get('action', '?')}",
        f"  {instructions}",
    ] + facts)


def _fmt_foundry_record_spend(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Spend{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
            f"  {_DIM}{r.get('hint', '')}{_RESET}",
        ])
    row = r.get("recorded") or {}
    total = r.get("total") or {}
    minutes = int(total.get("duration_ms", 0) // 60000)
    lines = [
        f"  {_BWHITE}Agent:{_RESET}   {row.get('agent', '?')} @ {row.get('phase', '?')}"
        f" {_DIM}(cycle {row.get('cycle', '?')}){_RESET}",
        f"  {_BWHITE}Recorded:{_RESET} {row.get('tokens', 0):,} tokens  "
        f"{int(row.get('duration_ms', 0) // 1000)}s",
        f"  {_BWHITE}Run total:{_RESET} {total.get('tokens', 0):,} tokens  {minutes}m  "
        f"over {total.get('agents', 0)} agent(s)",
    ]
    unreported = r.get("unreported_dispatches") or []
    if unreported:
        lines.append(
            f"  {_BWHITE}Unreported:{_RESET} {_BYELLOW}{len(unreported)}{_RESET} "
            f"{_DIM}dispatch(es) still have no spend record — nothing blocks on them{_RESET}"
        )
    if row.get("ledger_problem"):
        lines.append(f"  {_BYELLOW}Ledger:{_RESET} {row['ledger_problem']}")
    # D-004: a coercion the result names but the display swallows is still a
    # silent mis-attribution — the lead reads THIS box, not the raw dict. Beside
    # `ledger_problem`, which is the same kind of fact: something is not as you
    # typed it, and nothing is blocked.
    for warning in r.get("warnings") or []:
        lines.append(f"  {_BYELLOW}Note:{_RESET} {_DIM}{warning}{_RESET}")
    return _foundry_display("F O U N D R Y  Spend recorded", lines)


def _fmt_foundry_report(r: dict) -> str:
    if r.get("ok") is False or r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Report{_RESET}", [
            f"  {_RED}{r.get('error', 'the report could not be generated')}{_RESET}",
            f"  {_DIM}{r.get('hint', '')}{_RESET}",
        ])
    sections = r.get("sections") or []
    return _foundry_display("F O U N D R Y  Report generated", [
        f"  {_BWHITE}Markdown:{_RESET} {_short_path(str(r.get('report_md', '?')))}",
        f"  {_BWHITE}JSON:{_RESET}     {_short_path(str(r.get('report_json', '?')))}",
        f"  {_BWHITE}Sections:{_RESET} {len(sections)} — {', '.join(sections)}",
        f"  {_DIM}Append prose below any section if you like; you may not omit one."
        f" Foundry-Phase(phase='done') refuses while any is missing.{_RESET}",
    ])


def _fmt_foundry_register_team(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Team Registration Failed{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
            f"  {_DIM}{r.get('hint', '')}{_RESET}",
        ])
    team = r.get("registered", "?")
    total = r.get("total_teams", 0)
    return _foundry_display("F O U N D R Y  Team Registered", [
        f"  {_BWHITE}Team:{_RESET}   {_BCYAN}{team}{_RESET}",
        f"  {_BWHITE}Active:{_RESET} {total}",
    ])


def _fmt_foundry_unregister_team(r: dict) -> str:
    if r.get("error"):
        phase = r.get("phase", "")
        error = r["error"]
        hint = r.get("hint", "")
        lines = [f"  {_RED}{error}{_RESET}"]

        # Show live panes if that's why we blocked
        live = r.get("live_panes", [])
        if live:
            lines.append(f"  {_BWHITE}Live panes:{_RESET}")
            for title in live[:5]:
                lines.append(f"    {_BYELLOW}{title}{_RESET}")

        if hint:
            lines.append(f"  {_DIM}{hint}{_RESET}")

        title = "Team Teardown Blocked"
        if phase == "team_dir_exists":
            title = "TeamDelete Not Called"
        elif phase == "live_teammates":
            title = "Teammates Still Alive"
        elif phase == "cleanup_failed":
            title = "Pane Cleanup Failed"

        return _foundry_display(f"F O U N D R Y  {_BRED}{title}{_RESET}", lines)

    team = r.get("unregistered", "?")
    remaining = r.get("remaining_teams", 0)
    tmux_killed = r.get("tmux_panes_killed", 0)

    lines = [
        f"  {_BWHITE}Team:{_RESET}      {team}",
        f"  {_BWHITE}Remaining:{_RESET} {remaining}",
    ]
    if tmux_killed > 0:
        lines.append(f"  {_BWHITE}Tmux:{_RESET}      {_BGREEN}{tmux_killed} zombie pane(s) killed{_RESET}")
    lines.append(f"  {_BWHITE}Clean:{_RESET}     {_BGREEN}verified{_RESET}")

    return _foundry_display("F O U N D R Y  Team Unregistered", lines)


def _fmt_foundry_mark_defect_fixed(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    defect_id = r.get("defect_id", "?")
    cycle = r.get("fixed_in_cycle", "?")
    remaining = r.get("remaining_open", 0)
    return _foundry_display(f"F O U N D R Y  Defect Fixed: {_BGREEN}{defect_id}{_RESET}", [
        f"  Cycle:     {cycle}",
        f"  Remaining: {_BYELLOW}{remaining}{_RESET} open",
    ])


def _fmt_foundry_sync_defects(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    cycle = r.get("cycle", "?")
    added = r.get("added", 0)
    reopened = r.get("reopened", 0)
    total_open = r.get("total_open", 0)
    regressions = r.get("regressions", [])

    retiered = r.get("retiered", 0)

    lines = [
        f"  Added:      {_BYELLOW}+{added}{_RESET}",
        f"  Reopened:   {_RED}{reopened}{_RESET}" if reopened > 0 else f"  Reopened:   0",
        # D-100: beside Added and Reopened, because it is the third thing a
        # batch can do to the ledger and the only one the screen did not say.
        # A batch that re-tiered one record and appended none rendered
        # "Added: +0  Reopened: 0", which reads as "nothing happened".
        f"  Re-tiered:  {_BGREEN}{retiered}{_RESET}" if retiered else "  Re-tiered:  0",
        f"  Total open: {_BWHITE}{total_open}{_RESET}",
    ]
    if (retier := _retier_line(r)):
        lines.append(retier)
    if regressions:
        lines.append(f"  {_BRED}Regressions: {', '.join(regressions)}{_RESET}")

    return _foundry_display(f"F O U N D R Y  Defect Sync: Cycle {cycle}", lines)


def _fmt_foundry_defects_to_tasks(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    tasks = r.get("tasks", [])
    if not tasks:
        return _foundry_display("F O U N D R Y  Task Generation", [
            f"  {_BGREEN}No open defects{_RESET} \u2014 nothing to generate.",
        ])

    count = r.get("count", len(tasks))
    lines = []
    for i, t in enumerate(tasks, 1):
        ids = ", ".join(t.get("defect_ids", []))
        if t.get("structural"):
            # An escalated class is ONE packet, not N \u2014 make that visible, since
            # the lead's dispatch shape changes because of it.
            marker = f"{_BRED}\u25c9{_RESET}"
        elif t.get("regression"):
            marker = f"{_BRED}\u25b2{_RESET}"
        else:
            marker = f"{_CYAN}\u25b6{_RESET}"
        if t.get("structural"):
            lines.append(
                f"  {marker} {_BWHITE}{i}.{_RESET} {_BRED}STRUCTURAL{_RESET} "
                f"'{t.get('defect_class', '?')}' \u2014 {t.get('consecutive_cycles', '?')} "
                f"consecutive cycles, {len(t.get('defect_ids', []))} open [{ids}]"
            )
        else:
            lines.append(f"  {marker} {_BWHITE}{i}.{_RESET} [{ids}] {t.get('description', '?')[:40]}")
        files = t.get("files", [])
        if files:
            lines.append(f"     {_DIM}Files: {', '.join(files[:3])}{_RESET}")

    return _foundry_display(f"F O U N D R Y  Tasks Generated: {_BWHITE}{count}{_RESET}", lines)


def _fmt_foundry_mark_stream(r: dict) -> str:
    if r.get("error"):
        # Compact display for stream failures — these are expected during normal flow
        reason = r.get("error", "")
        return f"{_DIM}Stream: {reason}{_RESET}"
    stream = r.get("stream", "?").upper()
    coverage = r.get("coverage", "?")
    items = r.get("items_checked", 0)
    total = r.get("items_total", 0)
    findings = r.get("findings", 0)
    warning = r.get("warning", "")

    findings_color = _BGREEN if findings == 0 else _BYELLOW
    lines = [
        f"  Checked:  {items}/{total}  ({coverage})",
        f"  Findings: {findings_color}{findings}{_RESET}",
    ]
    if warning:
        lines.append(f"  {_BYELLOW}{warning}{_RESET}")

    return _foundry_display(f"F O U N D R Y  Stream Complete: {_BGREEN}{stream}{_RESET}", lines)


def _fmt_foundry_get_context(r: dict) -> str:
    if not r.get("initialized"):
        return _foundry_display("F O U N D R Y", [
            f"  {_DIM}No active foundry run.{_RESET}",
            f"  Call Foundry-Init to start a new run.",
        ])

    state = r.get("state", {})
    defects = r.get("defects", {})
    verdicts = r.get("verdicts", {})

    phase = state.get("phase", "?")
    phase_name = _PHASE_NAMES.get(phase, "")

    lines = [
        f"  {_BWHITE}Spec:{_RESET}     {_short_path(state.get('spec_path', '')) or 'none'}",
        f"  {_BWHITE}Duration:{_RESET} {state.get('total_duration', 'in progress')}",
        "",
        f"  {_BWHITE}Defects:{_RESET}  {defects.get('total', 0)} total  "
        f"{_BYELLOW}{defects.get('open', 0)} open{_RESET}  "
        f"{_BGREEN}{defects.get('fixed', 0)} fixed{_RESET}  "
        f"{_BRED}{defects.get('regressions', 0)} regressed{_RESET}",
    ]

    v_total = verdicts.get("total", 0)
    v_verified = verdicts.get("verified", 0)
    if v_total > 0:
        lines.append(f"  {_BWHITE}Verdicts:{_RESET} {_bar(v_verified, v_total, width=20)}")
    else:
        lines.append(f"  {_BWHITE}Verdicts:{_RESET} {_DIM}none yet{_RESET}")

    streams = r.get("streams", {})
    if streams:
        req = streams.get("required", [])
        missing = streams.get("missing", "").split()
        if req:
            icons = []
            # FR-013: rendered from the canonical stream vocabulary rather than
            # from a local copy of the names. The hardcoded five silently hid
            # every stream added since — a required stream this list did not
            # mention simply never appeared in the status line.
            for s in sorted(STREAM_WIRE_IDS):
                if s in req:
                    if s not in missing:
                        icons.append(f"[{_GREEN}\u2713{_RESET}]{s}")
                    else:
                        icons.append(f"[{_DIM} {_RESET}]{s}")
            lines.append(f"  {_BWHITE}Streams:{_RESET}  {' '.join(icons)}")

    teams = r.get("active_teams", {})
    if teams.get("active"):
        lines.append(f"  {_BWHITE}Teams:{_RESET}    {_BCYAN}{', '.join(teams['teams'])}{_RESET}")

    return _foundry_display(f"F O U N D R Y  {_BCYAN}{phase} {phase_name}{_RESET}  Cycle: {state.get('cycle', 0)}", lines)


def _fmt_foundry_inject_directive(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    priority = r.get("priority", "normal")
    label = f"{_BRED}URGENT{_RESET}" if priority == "urgent" else "normal"
    return _foundry_display("F O U N D R Y  Directive Injected", [
        f"  Priority: {label}",
        f"  {_DIM}{r.get('message', '')}{_RESET}",
    ])


def _fmt_foundry_clear_directives(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    cleared = r.get("cleared_count", 0)
    lines = [f"  {_BWHITE}Cleared:{_RESET}  {cleared}"]
    if cleared:
        lines.append(
            f"  {_BWHITE}Urgent:{_RESET}   {r.get('urgent_cleared', 0)}"
            f"   {_BWHITE}Normal:{_RESET} {r.get('normal_cleared', 0)}"
        )
        # FR-019: clearing a directive must not destroy the record of it.
        lines.append(f"  {_BWHITE}Record:{_RESET}   {_short_path(r.get('record', ''))}")
    else:
        lines.append(f"  {_DIM}{r.get('message', 'No active directives to clear')}{_RESET}")
    return _foundry_display("F O U N D R Y  Directives Cleared", lines)


# ── Forge-Spec formatters ────────────────────────────────────────────────────


_FORGE_PHASE_ICONS = {
    "S0": "UNDERSTAND",
    "S1": "DECOMPOSE",
    "S2": "PLAN",
    "S3": "VALIDATE",
    "READY": "READY",
}


def _fmt_forge_spec_start(r: dict) -> str:
    if r.get("error"):
        return _mini_box("Forge-Spec Error", [f"  {_RED}{r['error']}{_RESET}"], color=_BRED)
    project = r.get("project_name", "?")
    slug = r.get("slug", "?")
    resumed = r.get("resumed", False)
    phase = r.get("phase", "S0")
    phase_name = _FORGE_PHASE_ICONS.get(phase, phase)
    action = "Resumed" if resumed else "Initialized"
    color = _BCYAN if not resumed else _BYELLOW

    lines = [
        f"  {_BWHITE}Project:{_RESET}  {project}",
        f"  {_BWHITE}Slug:{_RESET}     {slug}",
        f"  {_BWHITE}Dir:{_RESET}      {_short_path(r.get('project_dir', '?'))}",
        f"  {_BWHITE}Phase:{_RESET}    {_BCYAN}{phase} {phase_name}{_RESET}",
    ]
    if not resumed:
        dirs = r.get("dirs_created", [])
        if dirs:
            lines.append(f"  {_BWHITE}Created:{_RESET}  {', '.join(dirs)}")

    return _box(f"Forge-Spec {action}", lines, color=color)


def _fmt_forge_spec_check(r: dict) -> str:
    if r.get("error"):
        return _mini_box("Forge-Spec Error", [f"  {_RED}{r['error']}{_RESET}"], color=_BRED)
    action = r.get("action", "?")
    found = r.get("found", False)
    phase = r.get("phase", "?")
    phase_name = _FORGE_PHASE_ICONS.get(phase, phase)

    lines = [
        f"  {_BWHITE}Check:{_RESET}  {action}",
        f"  {_BWHITE}Found:{_RESET}  {_status_icon(found)} {'yes' if found else 'no'}",
        f"  {_BWHITE}Phase:{_RESET}  {_BCYAN}{phase} {phase_name}{_RESET}",
    ]

    if action == "codebase" and found:
        files = r.get("files", [])
        if files:
            lines.append(f"  {_BWHITE}Files:{_RESET}  {', '.join(files[:5])}")
    elif action == "decompose" and found:
        splits = r.get("splits", [])
        lines.append(f"  {_BWHITE}Splits:{_RESET} {r.get('count', 0)} domain(s)")
        for s in splits[:5]:
            lines.append(f"    {_DIM}{s}{_RESET}")
    elif action == "spec":
        if r.get("converted"):
            lines.append(f"  {_BWHITE}Reqs:{_RESET}   {_BGREEN}{r.get('requirement_count', 0)}{_RESET} "
                         f"(NFR: {r.get('nfr_count', 0)}, AC: {r.get('ac_count', 0)})")
            lines.append(f"  {_BWHITE}Arch:{_RESET}   {r.get('arch_sections', 0)} section(s)")
            lines.append(f"  {_BWHITE}Spec:{_RESET}   {_short_path(r.get('spec_path', '?'))}")
            lines.append(f"  {_BWHITE}Plan:{_RESET}   {_short_path(r.get('plan_path', '?'))}")

    hint = r.get("hint", "")
    if hint:
        lines.append(f"  {_BYELLOW}Hint:{_RESET} {hint}")

    color = _BGREEN if found else _BYELLOW
    return _box(f"Forge-Spec Check: {action}", lines, color=color)


def _fmt_forge_spec_status(r: dict) -> str:
    if r.get("error"):
        return _mini_box("Forge-Spec Error", [f"  {_RED}{r['error']}{_RESET}"], color=_BRED)
    project = r.get("project_name", "?")
    phase = r.get("phase", "?")
    phase_name = _FORGE_PHASE_ICONS.get(phase, phase)
    ready = r.get("foundry_ready", False)

    lines = [
        f"  {_BWHITE}Project:{_RESET} {project}",
        f"  {_BWHITE}Phase:{_RESET}   {_BCYAN}{phase} {phase_name}{_RESET}",
        f"  {_BWHITE}Ready:{_RESET}   {_status_icon(ready)} {'yes' if ready else 'no'}",
        "",
    ]

    checklist = r.get("checklist", [])
    for item in checklist:
        status = item.get("status", "pending")
        if status == "complete":
            icon = f"{_GREEN}\u2713{_RESET}"
        elif status == "skipped":
            icon = f"{_DIM}-{_RESET}"
        else:
            icon = f"{_DIM} {_RESET}"
        detail = ""
        if "splits" in item:
            detail = f"  ({item['splits']} splits)"
        if "requirements" in item:
            detail = f"  ({item['requirements']} requirements)"
        if item.get("specs_total", 0) > 0:
            detail = f"  ({item['specs_done']}/{item['specs_total']} specs)"
        lines.append(f"  [{icon}] {item.get('phase', '?')}{detail}")

    if ready:
        lines.append("")
        lines.append(f"  {_BGREEN}Run:{_RESET} /foundry --spec {_short_path(r.get('foundry_spec_path', '?'))}")

    color = _BGREEN if ready else _BCYAN
    return _box("Forge-Spec Pipeline", lines, color=color)


# ── Router ───────────────────────────────────────────────────────────────────

_FORMATTERS: dict[str, callable] = {
    "Validate-Report": _fmt_validate_report,
    "Verify-Citations": _fmt_verify_citations,
    "Foundry-Init": _fmt_foundry_init,
    "Foundry-Defect": _fmt_foundry_add_defect,
    "Foundry-Defects": _fmt_foundry_query_defects,
    "Foundry-Verdict": _fmt_foundry_add_verdict,
    "Foundry-Coverage": _fmt_foundry_verify_coverage,
    "Foundry-Gate": _fmt_foundry_gate,
    "Foundry-Phase": _fmt_foundry_mark_phase_complete,
    "Foundry-Next": _fmt_foundry_next_action,
    "Foundry-Team-Up": _fmt_foundry_register_team,
    "Foundry-Team-Down": _fmt_foundry_unregister_team,
    "Foundry-Fix": _fmt_foundry_mark_defect_fixed,
    "Foundry-Sync": _fmt_foundry_sync_defects,
    "Foundry-Tasks": _fmt_foundry_defects_to_tasks,
    "Foundry-Stream": _fmt_foundry_mark_stream,
    "Foundry-Context": _fmt_foundry_get_context,
    "Foundry-Directive": _fmt_foundry_inject_directive,
    "Foundry-Clear": _fmt_foundry_clear_directives,
    "Foundry-Spend": _fmt_foundry_record_spend,
    "Foundry-Report": _fmt_foundry_report,
    "Forge-Spec-Start": _fmt_forge_spec_start,
    "Forge-Spec-Check": _fmt_forge_spec_check,
    "Forge-Spec-Status": _fmt_forge_spec_status,
}


# ── The named-refusal guarantee ──────────────────────────────────────────────
#
# D-157 — THE HANDLER REFUSED AND THE SCREEN SAID "?".
#
# `foundry_next_action` runs `_artifact_guard` first and, on a run whose
# DECLARED EXTERNAL INPUT (the spec at state.json's `spec_path`, outside the run
# dir) is undecodable, it returned the house refusal naming the file, the cause
# and the repair hint. What the operator SAW was a 109-character banner with a
# literal '?' for the phase and 'Action: ?' for the imperative, because
# `_fmt_foundry_next_action` reads `display` / `instructions` / `phase` /
# `action` and a refusal carries none of them. A normal-priority directive filed
# before the corruption vanished from the same render with nothing said about
# why (FR-019 / AC-004). The refusal existed at every layer except the one a
# human reads.
#
# AND IT WAS NEVER ONE FORMATTER. Driven across the whole table with one house
# refusal, FIVE of the twenty-two dropped it outright -- Foundry-Next,
# Foundry-Context (the adjacent door on the same fixture), Foundry-Init,
# Validate-Report and Verify-Citations -- while the other seventeen each carry
# their own hand-written `if r.get("error")` branch. That is the escalated class
# living inside the renderer: whether a refusal reaches the screen was decided
# once per formatter, so a formatter added tomorrow decides it again, and a
# formatter that forgets fails in silence.
#
# So the guarantee is stated ONCE, here, as a POST-CONDITION over whatever the
# formatter produced: a result that NAMES a refusal is rendered CARRYING that
# refusal, or the router renders the house refusal block itself. Membership is
# read off the RESULT ("does it name a refusal"), not off a list of formatters,
# so no formatter can sit outside it -- and the seventeen that already render
# their own refusal are untouched, because their output already contains the
# text. Over-rendering is recoverable; under-rendering is this defect.
#
# The JSON fallback is deliberately outside the check: it emits the whole result
# verbatim, so it cannot drop anything, and `call_tool`'s outermost net depends
# on an unformatted refusal staying machine-readable JSON.

#: The keys a handler names a refusal in. `error` is the house shape (an `error`
#: naming the offending value, a `hint` naming the action); `reason` is the
#: gate's variant -- `foundry_gate` renames the guard's `error` into `reason`
#: and keeps `hint`/`corrupt_artifacts`, so a refusal reaches the renderer under
#: either key and a check that knew only one of them would be half a guarantee.
_REFUSAL_TEXT_KEYS = ("error", "reason")  # 2 keys


def _named_refusal(result: object) -> str | None:
    """The refusal text a handler named, or None when it named none."""
    if not isinstance(result, dict):
        return None
    for key in _REFUSAL_TEXT_KEYS:
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _launch_command_lines(result: dict) -> list[str]:
    """The shell command a refusal named, rendered so it can be copied.

    D-011 — A HINT THAT SAYS "THE COMMAND ABOVE" NEEDS A COMMAND ABOVE.
    ------------------------------------------------------------------
    `foundry_init`'s self-target refusal sets `launch_command` into the result
    dict at two sites and its `hint` reads "Quit, relaunch with the command
    above". Nothing rendered it: `server.py#call_tool` returns only
    `format_result(...)`, so the key never crossed the MCP boundary and the
    only readers in the whole tree were test assertions. Driven against a
    9.9.9 tree, the operator's screen carried no `claude --plugin-dir`
    substring at all — a refusal that names the remedy in a key nobody prints
    is a refusal with no remedy.

    Rendered as its own indented line rather than folded into the hint prose,
    because the entire point is that it is copied verbatim into a shell.
    """
    command = result.get("launch_command")
    if not isinstance(command, str) or not command.strip():
        return []
    return [
        "",
        f"  {_BWHITE}Relaunch with:{_RESET}",
        f"    {_BCYAN}{command.strip()}{_RESET}",
    ]


def _house_refusal_display(tool_name: str, result: dict, refusal: str) -> str:
    """The house refusal, rendered for a tool whose formatter dropped it.

    Same rungs the refusal dict carries: what is wrong, WHICH files, the
    command that fixes it, and what to do -- so the operator learns the file to
    repair instead of reading a banner about a phase the tool never got far
    enough to know.

    `launch_command` is rendered HERE, and not only in the one formatter whose
    tool emits it today, because this is the net every formatter falls into:
    any tool that names a remedy command in its refusal is rendered by this
    function the moment its own formatter does not repeat the refusal text.
    Fixing it one formatter up would have left the net dropping the same field
    for the next tool that names one.
    """
    lines = [f"  {_RED}{refusal}{_RESET}"]
    corrupt = result.get("corrupt_artifacts")
    if isinstance(corrupt, list):
        for artifact in corrupt:
            lines.append(f"    {_BYELLOW}{artifact}{_RESET}")
    lines.extend(_launch_command_lines(result))
    hint = result.get("hint")
    if isinstance(hint, str) and hint.strip():
        lines.append(f"  {_DIM}{hint}{_RESET}")
    return _foundry_display(f"F O U N D R Y  {_BRED}{tool_name} refused{_RESET}", lines)


def format_result(tool_name: str, result: dict) -> str:
    """Format a tool result for display.

    Returns a visually formatted string if a formatter exists for the tool,
    otherwise falls back to indented JSON. A refusal the handler named always
    survives to the output -- see the note above.
    """
    formatter = _FORMATTERS.get(tool_name)

    if formatter:
        try:
            rendered = formatter(result)
        except Exception:
            pass  # Fall through to JSON
        else:
            refusal = _named_refusal(result)
            if refusal is not None and refusal not in rendered:
                return _house_refusal_display(tool_name, result, refusal)
            return rendered

    return json.dumps(result, indent=2)
