"""Rich display formatting for MCP tool results.

Transforms raw dicts into visually appealing terminal output with
ANSI colors and pixel-art hammer branding for foundry tools.

Falls back to JSON for unknown tool names.
"""

from __future__ import annotations

import json
import os


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


def _fmt_foundry_init(r: dict) -> str:
    if "display" in r:
        return r["display"]
    return _foundry_display("F O U N D R Y  Initialized", [
        f"  {_BWHITE}Dir:{_RESET}    {_short_path(r.get('foundry_dir', '?'))}",
        f"  {_BWHITE}Name:{_RESET}   {r.get('run_name', '?')}",
        f"  {_BWHITE}Files:{_RESET}  {', '.join(r.get('files_created', []))}",
        f"  {_BWHITE}Spec:{_RESET}   {'copied' if r.get('spec_copied') else 'none'}",
    ])


def _fmt_foundry_add_defect(r: dict) -> str:
    if r.get("error"):
        return _foundry_display(f"F O U N D R Y  {_BRED}Error{_RESET}", [
            f"  {_RED}{r['error']}{_RESET}",
        ])
    defect_id = r.get("defect_id", "?")
    total = r.get("total_defects", 0)
    open_count = r.get("open_defects", 0)
    return _foundry_display(f"F O U N D R Y  Defect: {_BYELLOW}{defect_id}{_RESET}", [
        f"  Total: {total}  Open: {_BYELLOW}{open_count}{_RESET}",
    ])


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


def _fmt_foundry_next_action(r: dict) -> str:
    # Always show BOTH the pixel-art status header (from the `display` field)
    # AND the imperative instructions (which lead with a "YOUR NEXT CALL:"
    # line from Phase 6).
    instructions = r.get("instructions", "")
    pre_rendered = r.get("display")
    if pre_rendered and instructions:
        return f"{pre_rendered}\n\n{instructions}"
    if pre_rendered:
        return pre_rendered
    return _foundry_display(f"F O U N D R Y  {r.get('phase', '?')}", [
        f"  {_BWHITE}Action:{_RESET}  {r.get('action', '?')}",
        f"  {instructions}",
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

    lines = [
        f"  Added:      {_BYELLOW}+{added}{_RESET}",
        f"  Reopened:   {_RED}{reopened}{_RESET}" if reopened > 0 else f"  Reopened:   0",
        f"  Total open: {_BWHITE}{total_open}{_RESET}",
    ]
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
        if t.get("regression"):
            marker = f"{_BRED}\u25b2{_RESET}"
        else:
            marker = f"{_CYAN}\u25b6{_RESET}"
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
            for s in ["trace", "prove", "sight", "test", "probe"]:
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
    return _foundry_display("F O U N D R Y  Directives Cleared", [
        f"  {r.get('message', 'All directives cleared.')}",
    ])


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
    "Forge-Spec-Start": _fmt_forge_spec_start,
    "Forge-Spec-Check": _fmt_forge_spec_check,
    "Forge-Spec-Status": _fmt_forge_spec_status,
}


def format_result(tool_name: str, result: dict) -> str:
    """Format a tool result for display.

    Returns a visually formatted string if a formatter exists for the tool,
    otherwise falls back to indented JSON.
    """
    formatter = _FORMATTERS.get(tool_name)

    if formatter:
        try:
            return formatter(result)
        except Exception:
            pass  # Fall through to JSON

    return json.dumps(result, indent=2)
