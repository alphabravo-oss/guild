#!/usr/bin/env python3
"""adhoc Stop hook — methodical-mode enforcement gates (v0.3.0).

Reads the Stop hook event JSON from stdin, walks the conversation transcript
to find the current turn's last assistant message and tool calls, then runs
four enforcement gates. Any gate firing blocks the Stop with a combined
reason message that Claude reads on the continued turn.

Gates:
  (a) Citations  — file:line citations in the response must be backed by a
                   Read/Grep call Claude made directly in this turn. One block
                   per turn (honors stop_hook_active liveness).

  (b) Uncertainty — response must NOT contain self-flagged uncertainty
                    tells like "not verified", "haven't checked", "I assumed".
                    One block per turn.

  (c) Grounding  — if the user's prompt looks codebase-shaped, the response
                   requires at least one Read/Grep/Glob call this turn.
                   No general-knowledge fallback. One block per turn.

  (d) Iterative Critic — substantive responses must pass an iterative
                         critic-dialog: rounds 1-2 use adhoc:fast-critic
                         (Haiku), rounds 3-5 escalate to adhoc:pre-stop-critic
                         (opus). Each critic runs a 10-item rule compliance
                         audit and sees prior-round feedback (continuity
                         check). Up to 5 rounds per turn. At round 5: HARD
                         block (no pass-through) until /adhoc:trust-me
                         consumed. Round counter replaces stop_hook_active
                         honoring for this gate ONLY.

One-shot bypass for (c) and (d): ~/.claude/.adhoc-trust-me (consumed on read).

Recursion guard: if the assistant response starts with EITHER critic sentinel
([adhoc:fast-critic-output] OR [adhoc:pre-stop-critic-output]), ALL gates
skip (this turn IS a critic, not the main Claude).

Mode files (all under ~/.claude/):
  .adhoc-citations-mode     (a)  absent/"default" / "off"
  .adhoc-uncertainty-mode   (b)  absent/"default" / "off"
  .adhoc-strict-mode        (c)+(d)  absent/"default" / "off"
  .adhoc-trust-me           one-shot bypass file for (c)+(d), consumed on read

Liveness model (v0.3.0):
  - Gates (a)/(b)/(c) honor stop_hook_active: one block per turn each
    (preserves v0.2.0 anti-loop safety)
  - Gate (d) uses transcript-based round counter: up to MAX_CRITIC_ROUNDS=5
    block-rounds per turn, then HARD block at round 5
  - Trust-me file consumed before any gate runs; if present, (c)+(d) skip
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# --- configuration --------------------------------------------------------

HOME = Path(os.environ.get("HOME", os.path.expanduser("~")))
CITATIONS_MODE_FILE = HOME / ".claude" / ".adhoc-citations-mode"
UNCERTAINTY_MODE_FILE = HOME / ".claude" / ".adhoc-uncertainty-mode"
STRICT_MODE_FILE = HOME / ".claude" / ".adhoc-strict-mode"
TRUST_ME_FILE = HOME / ".claude" / ".adhoc-trust-me"
LOG_FILE = HOME / ".claude" / ".adhoc-citations-log.jsonl"

# Critic agent identifiers — two tiers
FAST_CRITIC_AGENT_TYPE = "adhoc:fast-critic"
DEEP_CRITIC_AGENT_TYPE = "adhoc:pre-stop-critic"

# Sentinel markers — first line of each critic's output. Recursion guard.
FAST_CRITIC_SENTINEL = "[adhoc:fast-critic-output]"
DEEP_CRITIC_SENTINEL = "[adhoc:pre-stop-critic-output]"
CRITIC_SENTINELS = (FAST_CRITIC_SENTINEL, DEEP_CRITIC_SENTINEL)

# Iterative gate-dialog: max 5 rounds per turn.
# Rounds 1-2: fast-critic (Haiku). Rounds 3-5: pre-stop-critic (opus escalation).
# At round 5 reached without CONCUR: HARD block until trust-me consumed.
MAX_CRITIC_ROUNDS = 5
FAST_CRITIC_LAST_ROUND = 2  # rounds 1..2 use fast-critic; rounds 3..5 escalate

# Length thresholds (chars)
GROUNDING_MIN_RESPONSE_CHARS = 200
CRITIC_MIN_RESPONSE_CHARS = 200  # v0.3.0: lowered from 500 to catch short ritual responses
CLARIFYING_GATE_LENGTH = 1000  # asks shorter than this skip critic gate

# Code-file extensions (used by both citation regex and grounding heuristic)
CODE_EXT = (
    "go|ts|tsx|js|jsx|mjs|cjs|py|rb|rs|java|kt|swift|c|cc|cpp|cxx|h|hpp|"
    "cs|php|scala|clj|ex|exs|erl|hs|ml|fs|fsx|sh|bash|zsh|"
    "proto|sql|yaml|yml|toml|ini|json|jsonl|"
    "md|mdx|tf|hcl|dockerfile|"
    "html|css|scss|sass|less|vue|svelte"
)

CITATION_RE = re.compile(
    rf"(?P<path>(?:[\w\-./]+/)*[\w\-.]+\.(?:{CODE_EXT})):(?P<line>\d+)",
    re.IGNORECASE,
)

FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
DIFF_LINE_RE = re.compile(r"^[+\-@]{1,3}\s")

VERIFICATION_TOOLS = {"Read", "Grep", "Glob"}
AGENT_TOOLS = {"Task", "Agent"}

# (b) Uncertainty-tell scanner — narrow regex, only the specific failure mode
UNCERTAINTY_TELLS_RE = re.compile(
    r"\b("
    r"not\s+yet\s+verified|"
    r"haven't\s+(?:verified|checked|confirmed|read)|"
    r"have\s+not\s+(?:verified|checked|confirmed|read)|"
    r"didn't\s+(?:verify|check|confirm|read)|"
    r"did\s+not\s+(?:verify|check|confirm|read)|"
    r"unverified|"
    r"still\s+need\s+to\s+(?:verify|check|confirm)|"
    r"i\s+assumed|"
    r"i'm\s+assuming|"
    r"todo:\s*verify"
    r")\b",
    re.IGNORECASE,
)

# (c) Grounding — three independent signals that a prompt is "codebase-shaped"
CODEBASE_PATH_RE = re.compile(r"\b[\w\-.]+/[\w\-./]+\b")
CODEBASE_EXT_RE = re.compile(rf"\.(?:{CODE_EXT})\b", re.IGNORECASE)
CODEBASE_ACTION_RE = re.compile(
    r"\b("
    r"function|file|files|code|fix|implement|modify|add|remove|delete|"
    r"refactor|rename|where\s+is|how\s+does|"
    r"this\s+(?:codebase|project|repo|repository|file|function|module)|"
    r"the\s+(?:codebase|project|repo|function|file|code|module|handler|endpoint|hook|script|plugin|agent|skill|command)|"
    r"our\s+(?:codebase|project|repo)|"
    r"my\s+(?:codebase|project|repo)|"
    r"in\s+(?:the\s+)?codebase|"
    r"adhoc|foundry|forge|crew|tidy|e2e"
    r")\b",
    re.IGNORECASE,
)

# (c) Grounding — markers that the assistant is asking, not answering
CLARIFYING_QUESTION_RE = re.compile(
    r"\b("
    r"want\s+me\s+to|"
    r"should\s+i|"
    r"which\s+(?:would|of\s+these|do\s+you)|"
    r"do\s+you\s+(?:want|prefer|need)|"
    r"would\s+you\s+(?:like|prefer)|"
    r"is\s+that\s+(?:right|what\s+you\s+meant)"
    r")\b",
    re.IGNORECASE,
)

# (d) Critic — verdict extraction from critic output (used for gate decision)
CRITIC_VERDICT_RE = re.compile(
    r"^###\s+Verdict\s*\n\s*(CONCUR(?:\s+WITH\s+CAVEATS)?|PUSH\s+BACK|WRONG\s+SHAPE)\b",
    re.IGNORECASE | re.MULTILINE,
)

# (d) Critic — round number extraction
CRITIC_ROUND_RE = re.compile(
    r"^###\s+Round\s*\n\s*(\d+)\b",
    re.IGNORECASE | re.MULTILINE,
)

# (d) Critic — "Specific feedback for Claude" section extraction
CRITIC_FEEDBACK_RE = re.compile(
    r"^###\s+Specific\s+feedback\s+for\s+Claude\s*\n(.*?)(?=^###\s|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)

# (d) Critic — "Compliance audit" section extraction
CRITIC_AUDIT_RE = re.compile(
    r"^###\s+Compliance\s+audit\s*\n(.*?)(?=^###\s|\Z)",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)


# --- helpers --------------------------------------------------------------


def read_mode(path: Path) -> str:
    if not path.exists():
        return "default"
    try:
        return path.read_text().strip().lower() or "default"
    except OSError:
        return "default"


def consume_trust_me() -> bool:
    """Check and delete the one-shot trust-me flag. Returns True if it was present."""
    if not TRUST_ME_FILE.exists():
        return False
    try:
        TRUST_ME_FILE.unlink()
    except OSError:
        pass
    return True


def log_event(event: dict) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a") as fh:
            fh.write(json.dumps(event) + "\n")
    except OSError:
        pass


def strip_non_citations(text: str) -> str:
    """Remove fenced code blocks and diff lines before regex-scanning prose."""
    no_fences = FENCED_BLOCK_RE.sub("", text)
    kept = []
    for line in no_fences.splitlines():
        if DIFF_LINE_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def find_citations(text: str) -> list[tuple[str, str]]:
    cleaned = strip_non_citations(text)
    seen = set()
    out: list[tuple[str, str]] = []
    for m in CITATION_RE.finditer(cleaned):
        key = (m.group("path"), m.group("line"))
        if key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def collect_verified_paths(turn_messages: list[dict]) -> set[str]:
    """Paths Claude directly Read/Grep'd in this turn. Subagent calls excluded."""
    verified: set[str] = set()
    for msg in turn_messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name", "") not in VERIFICATION_TOOLS:
                continue
            params = block.get("input", {}) or {}
            for key in ("file_path", "path"):
                val = params.get(key)
                if isinstance(val, str) and val:
                    verified.add(val)
    return verified


def path_matches_verified(cited: str, verified: set[str]) -> bool:
    cited_norm = cited.lstrip("/")
    for v in verified:
        v_norm = v.lstrip("/")
        if v_norm == cited_norm or v_norm.endswith("/" + cited_norm):
            return True
        if cited_norm.endswith("/" + v_norm):
            return True
    return False


def collect_critic_calls_by_type(turn_messages: list[dict], agent_type: str) -> int:
    """Count Task/Agent tool calls with the given subagent_type in this turn."""
    count = 0
    for msg in turn_messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            if block.get("name") not in AGENT_TOOLS:
                continue
            params = block.get("input", {}) or {}
            if params.get("subagent_type") == agent_type:
                count += 1
    return count


def starts_with_any_sentinel(text: str) -> bool:
    """True if response begins with EITHER critic sentinel (recursion guard)."""
    stripped = text.lstrip()
    return any(stripped.startswith(s) for s in CRITIC_SENTINELS)


def critic_tier_for_round(round_num: int) -> str:
    """Map round number to critic agent type: rounds 1-2 fast, 3+ deep."""
    if round_num <= FAST_CRITIC_LAST_ROUND:
        return FAST_CRITIC_AGENT_TYPE
    return DEEP_CRITIC_AGENT_TYPE


def _extract_tool_result_text(content_blocks) -> str:
    """Flatten a tool_result content list to one string."""
    if isinstance(content_blocks, str):
        return content_blocks
    if isinstance(content_blocks, list):
        return "\n".join(
            b.get("text", "")
            for b in content_blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def parse_critic_output(text: str) -> dict:
    """Extract round, verdict, feedback, audit from a critic's output."""
    result = {"verdict": None, "round": None, "feedback": "", "audit": ""}
    if not text or not isinstance(text, str):
        return result
    if not any(s in text for s in CRITIC_SENTINELS):
        return result
    m = CRITIC_VERDICT_RE.search(text)
    if m:
        result["verdict"] = re.sub(r"\s+", " ", m.group(1).upper()).strip()
    m = CRITIC_ROUND_RE.search(text)
    if m:
        try:
            result["round"] = int(m.group(1))
        except (ValueError, TypeError):
            pass
    m = CRITIC_FEEDBACK_RE.search(text)
    if m:
        result["feedback"] = m.group(1).strip()
    m = CRITIC_AUDIT_RE.search(text)
    if m:
        result["audit"] = m.group(1).strip()
    return result


def find_all_critic_outputs(turn_messages: list[dict]) -> list[dict]:
    """Return parsed critic outputs from this turn, in chronological order."""
    out: list[dict] = []
    for msg in turn_messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_result":
                continue
            text = _extract_tool_result_text(block.get("content", ""))
            if not text or not any(s in text for s in CRITIC_SENTINELS):
                continue
            parsed = parse_critic_output(text)
            if parsed["verdict"]:
                out.append(parsed)
    return out


def find_last_user_prompt(messages: list[dict]) -> str:
    """Return the text of the most recent user-role message with actual user text."""
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            text_parts = []
            has_text = False
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "text":
                    text_parts.append(block.get("text", ""))
                    has_text = True
            if has_text:
                return "\n".join(text_parts)
    return ""


def summarize_tool_calls(turn_messages: list[dict]) -> str:
    """One-line-per-call summary of tool calls in this turn (for critic spawn input)."""
    summary_lines: list[str] = []
    for msg in turn_messages:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", [])
        if isinstance(content, str):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") != "tool_use":
                continue
            tool = block.get("name", "?")
            params = block.get("input", {}) or {}
            target = (
                params.get("file_path")
                or params.get("path")
                or params.get("pattern")
                or params.get("command")
                or params.get("subagent_type")
                or "<no-target>"
            )
            if isinstance(target, str) and len(target) > 120:
                target = target[:117] + "..."
            summary_lines.append(f"  - {tool}: {target}")
    if not summary_lines:
        return "  (none)"
    return "\n".join(summary_lines)


def is_codebase_shaped(prompt_text: str) -> bool:
    if CODEBASE_PATH_RE.search(prompt_text):
        return True
    if CODEBASE_EXT_RE.search(prompt_text):
        return True
    if CODEBASE_ACTION_RE.search(prompt_text):
        return True
    return False


def is_clarifying_response(response_text: str) -> bool:
    stripped = response_text.strip()
    if stripped.endswith("?"):
        return True
    if CLARIFYING_QUESTION_RE.search(stripped):
        return True
    return False


def load_transcript(path: str) -> list[dict]:
    try:
        msgs = []
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    msgs.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return msgs
    except OSError:
        return []


def slice_current_turn(messages: list[dict]) -> tuple[list[dict], dict | None]:
    """Slice from the most recent GENUINE user prompt to the last assistant message.

    A "genuine user prompt" is a user-role message containing real text content
    (the user typed something). User-role messages containing only tool_result
    blocks are INTERNAL to Claude's response process — they belong to the same
    turn and must be included in the slice. This was a bug in v0.2.0 that made
    multi-round critic counting fail.
    """
    if not messages:
        return [], None
    last_assistant = None
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            last_assistant = msg
            break
    if last_assistant is None:
        return [], None
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if msg is last_assistant:
            break
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        if isinstance(content, str):
            last_user_idx = i
            continue
        if isinstance(content, list):
            has_text = any(
                isinstance(b, dict) and b.get("type") == "text"
                for b in content
            )
            if has_text:
                last_user_idx = i
    turn_start = last_user_idx if last_user_idx >= 0 else 0
    return messages[turn_start:], last_assistant


def assistant_text(msg: dict) -> str:
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def format_prior_feedback(prior_outputs: list[dict]) -> str:
    """Format prior critic outputs for inclusion in the next round's spawn prompt."""
    if not prior_outputs:
        return ""
    lines: list[str] = []
    for i, p in enumerate(prior_outputs, start=1):
        round_label = p.get("round") or i
        verdict = p.get("verdict") or "(unknown)"
        lines.append(f"  Round {round_label} verdict: {verdict}")
        if p.get("audit"):
            audit_lines = [
                f"    {ln}" for ln in p["audit"].splitlines() if ln.strip()
            ]
            if audit_lines:
                lines.append("    Compliance audit:")
                lines.extend(audit_lines)
        if p.get("feedback"):
            fb_lines = [
                f"    {ln}" for ln in p["feedback"].splitlines() if ln.strip()
            ]
            if fb_lines:
                lines.append("    Specific feedback:")
                lines.extend(fb_lines)
    return "\n".join(lines)


# --- check functions ------------------------------------------------------


def check_citations(
    response_text: str, verified_paths: set[str]
) -> tuple[bool, str, list]:
    if read_mode(CITATIONS_MODE_FILE) == "off":
        return False, "", []
    citations = find_citations(response_text)
    unverified = [
        (path, line)
        for (path, line) in citations
        if not path_matches_verified(path, verified_paths)
    ]
    if not unverified:
        return False, "", citations
    cited_lines = "\n".join(f"  - {p}:{l}" for p, l in unverified)
    reason = (
        "[adhoc:citations] Response contains "
        f"{len(unverified)} file:line citation(s) that were NOT Read or Grep'd "
        "by you in this turn:\n"
        f"{cited_lines}\n"
        "Subagent (Task/Agent) calls do NOT count as direct verification — their "
        "internal Reads are not visible here. For each unverified citation, "
        "either:\n"
        "  1. Read the file yourself now and confirm the line is correct, or\n"
        "  2. Remove the citation from your response.\n"
        "Then continue. Do not stop until every file:line citation is backed "
        "by a Read/Grep tool call you made directly in this turn.\n"
        "(To disable this check: /adhoc:citations-off)"
    )
    return True, reason, unverified


def check_uncertainty(response_text: str) -> tuple[bool, str, list]:
    if read_mode(UNCERTAINTY_MODE_FILE) == "off":
        return False, "", []
    cleaned = strip_non_citations(response_text)
    hits = [m.group(0) for m in UNCERTAINTY_TELLS_RE.finditer(cleaned)]
    if not hits:
        return False, "", []
    sample = "\n".join(f'  - "{t}"' for t in hits[:5])
    reason = (
        f"[adhoc:uncertainty] Response contains {len(hits)} self-flagged "
        "uncertainty marker(s) — you are reporting back work you know is "
        "incomplete:\n"
        f"{sample}\n"
        "Either verify the flagged items now (Read/Grep) and update the "
        "response, or stop and ASK the user before continuing. Don't ship "
        "with known gaps.\n"
        "(To disable this check: /adhoc:uncertainty-off)"
    )
    return True, reason, hits


def check_grounding(
    response_text: str,
    user_prompt: str,
    verified_paths: set[str],
    strict_off: bool,
    trust_me_consumed: bool,
) -> tuple[bool, str, dict]:
    details: dict = {}
    if strict_off:
        details["skipped"] = "strict-off"
        return False, "", details
    if trust_me_consumed:
        details["skipped"] = "trust-me"
        return False, "", details
    if len(response_text) < GROUNDING_MIN_RESPONSE_CHARS:
        details["skipped"] = f"response<{GROUNDING_MIN_RESPONSE_CHARS}"
        return False, "", details
    if is_clarifying_response(response_text):
        details["skipped"] = "response-is-clarifying"
        return False, "", details
    if not is_codebase_shaped(user_prompt):
        details["skipped"] = "prompt-not-codebase-shaped"
        return False, "", details
    if verified_paths:
        details["skipped"] = "tool-calls-made"
        return False, "", details
    reason = (
        "[adhoc:grounding] The user's prompt looks codebase-shaped (mentions "
        "code, files, project terms, or this marketplace's plugin names) but "
        "you made ZERO Read/Grep/Glob calls this turn. Methodical-mode "
        "requires grounding codebase claims in actual code, not training-data "
        "inference. Either:\n"
        "  1. Read or Grep the relevant files now and update the response, or\n"
        '  2. Reply with a clarifying question instead of an answer '
        '("want me to first check X?"). Don\'t answer from inference.\n'
        "One-shot bypass for this turn only: /adhoc:trust-me. Disable for "
        "the whole session: /adhoc:strict-off."
    )
    details["blocked"] = True
    return True, reason, details


def check_critic(
    response_text: str,
    user_prompt: str,
    turn_messages: list[dict],
    verified_paths: set[str],
    strict_off: bool,
    trust_me_consumed: bool,
) -> tuple[bool, str, dict]:
    """Layer (d) — iterative two-tier critic gate-dialog.

    Up to MAX_CRITIC_ROUNDS rounds per turn:
      - Rounds 1..FAST_CRITIC_LAST_ROUND: require adhoc:fast-critic call
      - Rounds FAST_CRITIC_LAST_ROUND+1..MAX_CRITIC_ROUNDS: require adhoc:pre-stop-critic

    Gate passes when most recent critic returns CONCUR or CONCUR WITH CAVEATS.
    At MAX_CRITIC_ROUNDS with non-CONCUR: HARD block (no pass-through) until
    trust-me consumed.
    """
    details: dict = {}
    if strict_off:
        details["skipped"] = "strict-off"
        return False, "", details
    if trust_me_consumed:
        details["skipped"] = "trust-me"
        return False, "", details
    if len(response_text) < CRITIC_MIN_RESPONSE_CHARS:
        details["skipped"] = f"response<{CRITIC_MIN_RESPONSE_CHARS}"
        return False, "", details
    if (
        is_clarifying_response(response_text)
        and len(response_text) < CLARIFYING_GATE_LENGTH
    ):
        details["skipped"] = "short-clarifying-response"
        return False, "", details

    fast_count = collect_critic_calls_by_type(turn_messages, FAST_CRITIC_AGENT_TYPE)
    deep_count = collect_critic_calls_by_type(turn_messages, DEEP_CRITIC_AGENT_TYPE)
    rounds_completed = fast_count + deep_count
    next_round = rounds_completed + 1
    details["fast_critic_calls"] = fast_count
    details["deep_critic_calls"] = deep_count
    details["rounds_completed"] = rounds_completed
    details["next_round"] = next_round

    prior_outputs = find_all_critic_outputs(turn_messages)
    details["prior_verdicts"] = [p.get("verdict") for p in prior_outputs]

    if rounds_completed > 0:
        last_verdict = prior_outputs[-1].get("verdict") if prior_outputs else None
        details["last_verdict"] = last_verdict
        if last_verdict in ("CONCUR", "CONCUR WITH CAVEATS"):
            return False, "", details

    if rounds_completed >= MAX_CRITIC_ROUNDS:
        prior_summary = format_prior_feedback(prior_outputs)
        reason = (
            f"[adhoc:critic-hard-block] Round {MAX_CRITIC_ROUNDS} reached "
            "WITHOUT a CONCUR verdict from the methodical-mode gate-critic. "
            "Per your /adhoc:strict-on configuration, this Stop is HARD-blocked "
            "until /adhoc:trust-me is invoked by the user.\n\n"
            f"Accumulated critic flags across {MAX_CRITIC_ROUNDS} rounds:\n"
            f"{prior_summary}\n\n"
            "Required action: STOP attempting to revise the response. Instead, "
            "send the user a message stating:\n"
            "  1. You've been blocked by the methodical-mode gate-critic after "
            f"{MAX_CRITIC_ROUNDS} rounds.\n"
            "  2. The accumulated unresolved flags (summarized above).\n"
            "  3. Ask the user to either (a) guide you on what's still wrong, "
            "or (b) invoke /adhoc:trust-me to bypass the gate for this turn.\n\n"
            "Then WAIT for the user. Do not silently retry Stop. The Stop "
            "hook will continue to BLOCK every Stop attempt until the trust-me "
            "bypass is invoked or you abandon this turn."
        )
        details["hard_block"] = True
        return True, reason, details

    next_tier = critic_tier_for_round(next_round)
    tool_calls_summary = summarize_tool_calls(turn_messages)
    files_touched = sorted(verified_paths) if verified_paths else []
    files_summary = (
        "\n".join(f"  - {p}" for p in files_touched) if files_touched else "  (none)"
    )

    if rounds_completed == 0:
        reason = (
            f"[adhoc:critic] Round 1 of {MAX_CRITIC_ROUNDS} — methodical-mode "
            f"gate-critic required. Response is substantive ({len(response_text)} "
            f"chars). Spawn the {next_tier} subagent to run the 10-item rule "
            "compliance audit:\n\n"
            "  Agent({\n"
            f"    subagent_type: '{next_tier}',\n"
            "    description: 'Methodical-mode gate-critic round 1',\n"
            "    prompt: <see input contract below>\n"
            "  })\n\n"
            "Required spawn-prompt sections (label each clearly):\n"
            f"  - User prompt: {user_prompt[:600]}"
            f"{'...' if len(user_prompt) > 600 else ''}\n"
            "  - Draft response: <quote your full draft response here>\n"
            "  - Tool calls this turn:\n"
            f"{tool_calls_summary}\n"
            "  - Files Claude touched:\n"
            f"{files_summary}\n"
            "  - Round number: 1\n"
            "  (No prior-round feedback on round 1.)\n\n"
            "Critic returns CONCUR / CONCUR WITH CAVEATS / PUSH BACK / WRONG SHAPE.\n"
            "  - CONCUR or CONCUR WITH CAVEATS: response lands.\n"
            "  - PUSH BACK or WRONG SHAPE: revise based on the critic's "
            f"specific feedback, attempt Stop again. The hook will run round 2.\n"
            f"  - Maximum {MAX_CRITIC_ROUNDS} rounds per turn. At round "
            f"{MAX_CRITIC_ROUNDS}: HARD block until /adhoc:trust-me invoked.\n"
            "One-shot bypass: /adhoc:trust-me. Disable: /adhoc:strict-off."
        )
        details["blocked"] = True
        details["block_kind"] = "first-round-spawn"
        return True, reason, details

    prior_summary = format_prior_feedback(prior_outputs)
    escalation_note = ""
    if next_round == FAST_CRITIC_LAST_ROUND + 1:
        escalation_note = (
            f"\n  ESCALATION: rounds 1..{FAST_CRITIC_LAST_ROUND} used the "
            "fast-critic (Haiku). Rounds "
            f"{FAST_CRITIC_LAST_ROUND + 1}..{MAX_CRITIC_ROUNDS} use the "
            "deep-tier pre-stop-critic (opus). Persistent non-CONCUR from "
            "fast-critic triggered this escalation — the issues are real, "
            "not Haiku-tier judgment limits.\n"
        )
    reason = (
        f"[adhoc:critic] Round {next_round} of {MAX_CRITIC_ROUNDS} — prior "
        "round did NOT return CONCUR. Methodical-mode gate-critic requires "
        f"another round.{escalation_note}\n"
        "Prior-round feedback:\n"
        f"{prior_summary}\n\n"
        f"Spawn the {next_tier} subagent for round {next_round}:\n\n"
        "  Agent({\n"
        f"    subagent_type: '{next_tier}',\n"
        f"    description: 'Methodical-mode gate-critic round {next_round}',\n"
        "    prompt: <see input contract below>\n"
        "  })\n\n"
        "Required spawn-prompt sections:\n"
        f"  - User prompt: {user_prompt[:600]}"
        f"{'...' if len(user_prompt) > 600 else ''}\n"
        "  - Draft response: <quote your revised draft>\n"
        "  - Tool calls this turn:\n"
        f"{tool_calls_summary}\n"
        "  - Files Claude touched:\n"
        f"{files_summary}\n"
        f"  - Round number: {next_round}\n"
        "  - Prior-round feedback (CRITICAL — the critic uses this to check "
        "you actually addressed prior flags, not just rephrased the same "
        "dodge):\n"
        f"{prior_summary}\n\n"
        "The critic will flag each prior-round item as ADDRESSED, STILL "
        "FAILING, or REPHRASED-NOT-FIXED. Any non-ADDRESSED prior flag "
        "guarantees PUSH BACK.\n"
        f"At round {MAX_CRITIC_ROUNDS}: HARD block. Bypass: /adhoc:trust-me."
    )
    details["blocked"] = True
    details["block_kind"] = "next-round-spawn"
    details["escalation"] = next_round == FAST_CRITIC_LAST_ROUND + 1
    return True, reason, details


# --- main -----------------------------------------------------------------


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    stop_hook_active = bool(event.get("stop_hook_active"))

    transcript_path = event.get("transcript_path")
    if not transcript_path or not os.path.exists(transcript_path):
        return 0

    messages = load_transcript(transcript_path)
    turn_messages, last_assistant = slice_current_turn(messages)
    if last_assistant is None:
        return 0

    response_text = assistant_text(last_assistant)

    if starts_with_any_sentinel(response_text):
        log_event(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "session": event.get("session_id"),
                "decision": "pass",
                "reason": "critic-output-sentinel",
            }
        )
        return 0

    user_prompt = find_last_user_prompt(messages)
    verified_paths = collect_verified_paths(turn_messages)
    strict_off = read_mode(STRICT_MODE_FILE) == "off"
    trust_me_consumed = consume_trust_me() if not strict_off else False

    citations_blocked, citations_reason, citations_details = check_citations(
        response_text, verified_paths
    )
    uncertainty_blocked, uncertainty_reason, uncertainty_details = check_uncertainty(
        response_text
    )
    grounding_blocked, grounding_reason, grounding_details = check_grounding(
        response_text, user_prompt, verified_paths, strict_off, trust_me_consumed
    )
    critic_blocked, critic_reason, critic_details = check_critic(
        response_text,
        user_prompt,
        turn_messages,
        verified_paths,
        strict_off,
        trust_me_consumed,
    )

    if stop_hook_active:
        citations_blocked = False
        uncertainty_blocked = False
        grounding_blocked = False

    log_event(
        {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session": event.get("session_id"),
            "response_chars": len(response_text),
            "user_prompt_chars": len(user_prompt),
            "verified_paths_count": len(verified_paths),
            "trust_me_consumed": trust_me_consumed,
            "strict_off": strict_off,
            "stop_hook_active": stop_hook_active,
            "checks": {
                "citations": {
                    "blocked": citations_blocked,
                    "details": citations_details,
                },
                "uncertainty": {
                    "blocked": uncertainty_blocked,
                    "details": uncertainty_details,
                },
                "grounding": {
                    "blocked": grounding_blocked,
                    "details": grounding_details,
                },
                "critic": {
                    "blocked": critic_blocked,
                    "details": critic_details,
                },
            },
            "decision": "block"
            if any(
                [citations_blocked, uncertainty_blocked, grounding_blocked, critic_blocked]
            )
            else "pass",
        }
    )

    reasons = [
        r
        for r in (
            citations_reason if citations_blocked else "",
            uncertainty_reason if uncertainty_blocked else "",
            grounding_reason if grounding_blocked else "",
            critic_reason if critic_blocked else "",
        )
        if r
    ]
    if not reasons:
        return 0

    combined = "\n\n".join(reasons)
    print(json.dumps({"decision": "block", "reason": combined}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
