#!/usr/bin/env python3
"""tldr Stop hook — the gate that makes the ruleset enforcement rather than advice.

Every other part of this plugin talks to the model before it writes. Nothing
read what came out, so a long answer reached the user with the ruleset fully
loaded and fully ignored. This closes that: it measures the response that is
about to be delivered and blocks it when it busts the budget, which sends the
model back to cut it down.

What it measures, and what it deliberately does not:

  prose words   — the writing. Fenced code, tables and quoted blocks are
                  excluded, because a long function or a data table is content
                  the user asked for, not padding.
  headings      — a short answer does not need chapters.
  list items    — rule 9: past five items a list stops being a list.

Exemptions, in the order they are checked:

  1. tldr off, or a /tldr:verbose turn        — the ruleset does not apply
  2. ~/.claude/.tldr-gate == "off"            — gate disabled, ruleset still on
  3. ~/.claude/.tldr-trust-me                 — one-shot, consumed on read
  4. the user asked for depth                 — "explain", "walk me through",
                                                "why does", "full breakdown"…
  5. stop_hook_active                         — already blocked once this turn

That last one matters most. This hook never blocks the same turn twice, so a
model that cannot get under the budget still delivers something rather than
looping forever in front of a user who is waiting.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

HOME = Path(os.path.expanduser("~"))
STATE_FILE = HOME / ".claude" / ".tldr-state"
GATE_FILE = HOME / ".claude" / ".tldr-gate"
TRUST_ME_FILE = HOME / ".claude" / ".tldr-trust-me"
VERBOSE_TURN_FILE = HOME / ".claude" / ".tldr-verbose-turn"

# Budgets. A backstop against the answer that ran away, not a word counter
# enforcing a style guide — but it has to actually catch something. Measured
# against a real 207-reply session: 400 words caught 2% and let a 719-word
# five-heading answer through, 250 would have blocked half of a median-249
# session and become noise. 300 catches the quarter that genuinely ran long.
MAX_PROSE_WORDS = 300
MAX_HEADINGS = 3
MAX_LIST_ITEMS = 5

# A request for depth suspends the shape, per the ruleset's own exceptions.
DEPTH_PATTERNS = re.compile(
    r"\b("
    r"explain|walk me through|talk me through|why does|why is|why did|"
    r"full breakdown|in detail|deep dive|elaborate|write up|write-up|"
    r"document|documentation|teach me|help me understand|"
    r"what are my options|pros and cons|compare|trade[- ]?offs"
    r")\b",
    re.IGNORECASE,
)

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+\S")
LIST_ITEM = re.compile(r"^\s*([-*+]|\d+[.)])\s+\S")
TABLE_ROW = re.compile(r"^\s*\|")
QUOTE = re.compile(r"^\s*>")


def read_state(path: Path, default: str = "on") -> str:
    try:
        return path.read_text().strip() or default
    except OSError:
        return default


def consume(path: Path) -> bool:
    """Read a one-shot flag and remove it, so it cannot apply twice."""
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError:
        pass
    return True


def load_transcript(path: str) -> list[dict]:
    """Return the conversation messages, innermost shape first.

    A transcript line is an envelope — cwd, gitBranch, parentUuid, a uuid, and
    the message itself under "message". The role lives on the inner object, so
    reading `role` off the envelope finds nothing and every lookup below it
    returns empty. That is how this gate shipped silently allowing everything:
    it parsed 26,000 lines and matched none of them. Lines that carry no
    message at all — attachments, summaries, hook records — are dropped here
    rather than being checked for a role they never have.
    """
    messages: list[dict] = []
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                # Accept both shapes: the envelope Claude Code writes, and a
                # bare message, which is what the tests and older transcripts
                # use.
                message = entry.get("message")
                if isinstance(message, dict) and message.get("role"):
                    messages.append(message)
                elif entry.get("role"):
                    messages.append(entry)
    except OSError:
        return []
    return messages


def last_assistant_text(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "\n".join(parts)
    return ""


def last_user_prompt(messages: list[dict]) -> str:
    """The most recent thing the user actually typed.

    User-role messages carrying only tool results belong to the assistant's own
    turn, so they are skipped: they are not a request for anything.
    """
    for msg in reversed(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            ]
            if parts:
                return "\n".join(parts)
    return ""


class Measurement:
    def __init__(self) -> None:
        self.words = 0
        self.headings = 0
        self.longest_list = 0

    @property
    def over(self) -> list[str]:
        problems = []
        if self.words > MAX_PROSE_WORDS:
            problems.append(f"{self.words} words of prose (budget {MAX_PROSE_WORDS})")
        if self.headings > MAX_HEADINGS:
            problems.append(f"{self.headings} headings (budget {MAX_HEADINGS})")
        if self.longest_list > MAX_LIST_ITEMS:
            problems.append(
                f"a list of {self.longest_list} items (budget {MAX_LIST_ITEMS})"
            )
        return problems


def measure(text: str) -> Measurement:
    """Count the prose, ignoring the parts that are content rather than writing."""
    result = Measurement()
    in_fence = False
    run = 0
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if TABLE_ROW.match(line) or QUOTE.match(line):
            # A table is data and a quote is someone else's words. Neither is
            # the model being long-winded.
            continue
        if HEADING.match(line):
            result.headings += 1
            run = 0
            continue
        if LIST_ITEM.match(line):
            run += 1
            result.longest_list = max(result.longest_list, run)
        elif not line.strip():
            run = 0
        result.words += len(line.split())
    return result


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    # Already blocked once this turn. Never loop: a user waiting on an answer is
    # worse served by a hook that will not let one out than by a long one.
    if event.get("stop_hook_active"):
        return 0

    # The ruleset itself is suspended, so there is nothing to enforce. The
    # verbose marker is left by inject.sh, which clears the state file as it
    # grants the exemption.
    if read_state(STATE_FILE) == "off":
        return 0
    if consume(VERBOSE_TURN_FILE):
        return 0
    if read_state(GATE_FILE) == "off":
        return 0
    if consume(TRUST_ME_FILE):
        return 0

    transcript_path = event.get("transcript_path")
    if not transcript_path or not os.path.exists(transcript_path):
        return 0
    messages = load_transcript(transcript_path)
    if not messages:
        return 0

    prompt = last_user_prompt(messages)
    if DEPTH_PATTERNS.search(prompt):
        return 0

    response = last_assistant_text(messages)
    if not response.strip():
        return 0

    problems = measure(response).over
    if not problems:
        return 0

    reason = (
        "tldr length gate: this response is "
        + ", and ".join(problems)
        + ".\n\n"
        "Rewrite it shorter before answering. Keep the first line — the action "
        "or the answer itself. Then delete, in this order: any sentence that "
        "announces what you are about to do, any recap of what just happened, "
        "every aside, every option you are not recommending, and every heading "
        "the answer can be read without.\n\n"
        "Detail the user did not ask for is the thing to cut, not the detail "
        "they did. If the full version is genuinely needed, say so in one line "
        "and offer it rather than delivering it.\n\n"
        "(One block per turn — the next response is delivered either way. "
        "/tldr:verbose exempts a turn, /tldr:gate-off disables this gate.)"
    )
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
