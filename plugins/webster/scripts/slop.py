#!/usr/bin/env python3
"""Slop detector for documentation prose and diagrams.

Copy and residue rules retargeted from rendered HTML to markdown, plus tells specific to
documentation and to generated diagrams.

Prints findings as file:line. Exit 1 when anything at high severity fires, exit 2 when a
target is not there or cannot be read.
"""
import os, re, sys

TARGETS = sys.argv[1:] or ["docs"]

# The agent names the byline and the Co-Authored-By trailer both look for. One list because
# the trailer should recognise exactly the authors the byline already does, and two
# hand-copied alternations disagree the first time somebody adds a name to one of them.
AI_NAMES = (r"AI|LLM|Claude|GPT|Copilot|Cursor|Gemini|ChatGPT|Codex|Devin"
            r"|noreply@anthropic\.com")

# The BMP code points carrying Emoji_Presentation=Yes in emoji-data.txt 16.0, as 33 class
# entries. This replaced the block range U+2600-U+27BF, which swallowed ✓ (U+2713) and
# ➜ (U+279C) — not emoji at all — and ✔ (U+2714), which is text-presentation. A heading
# marked with a check mark was failing the gate as though it carried a rocket. The range
# also missed ⭐ (U+2B50), which sits above it and is emoji presentation.
# Written as escapes, not as the characters themselves, so each entry can be read straight
# against emoji-data.txt rather than guessed from a glyph the terminal may not render.
EMOJI_PRESENTATION_BMP = (
    "\u231a-\u231b\u23e9-\u23ec\u23f0\u23f3\u25fd-\u25fe"
    "\u2614-\u2615\u2648-\u2653\u267f\u2693\u26a1"
    "\u26aa-\u26ab\u26bd-\u26be\u26c4-\u26c5\u26ce\u26d4"
    "\u26ea\u26f2-\u26f3\u26f5\u26fa\u26fd"
    "\u2705\u270a-\u270b\u2728\u274c\u274e"
    "\u2753-\u2755\u2757\u2795-\u2797\u27b0\u27bf"
    "\u2b1b-\u2b1c\u2b50\u2b55"
)
# The supplementary half stays a block range because A-022 said to keep U+1F300-U+1FAFF, not
# because every code point in it is emoji. Whole blocks inside it carry no Emoji property at
# all in emoji-data.txt 16.0: Ornamental Dingbats (U+1F650-U+1F67F), Alchemical Symbols
# (U+1F700-U+1F77F) and Supplemental Arrows-C (U+1F800-U+1F8FF). Keeping them is an
# over-match that costs nothing a reader would notice, because nobody heads a section with
# an alchemical symbol. The BMP over-match above was not free in the same way: it caught
# ✓, a mark writers really do use, and failed the build on it.
# Bare ⬆ (U+2B06) is in neither half, by design.
EMOJI = "[\U0001F300-\U0001FAFF" + EMOJI_PRESENTATION_BMP + "]"

# id, severity, pattern, why. Patterns run per line against markdown source.
# CASE_SENSITIVE rules are matched without re.I, because their whole point is capitalisation.
RULES = [
 ("marketing-buzzword", "high",
  r"\b(supercharge|unleash|revolutioni[sz]e|seamlessly|seamless|effortlessly|effortless|game-chang\w+|cutting-edge|next-level|10x your|unlock the power)\b",
  "Superlatives that survive a find and replace of the product name say nothing about the product."),
 # robust, leverage, elevate and empower were alternatives in the rule above until they cost
 # this gate its credibility: at high severity they failed the build on ordinary release notes.
 # They are still worth naming, so they moved here rather than being deleted.
 ("weak-verb", "medium", r"\b(robust|leverage|elevate|empower)\b",
  "Verbs that sound like a claim without making one. Name what the thing does instead."),
 ("buzz-phrase", "high",
  r"\b(the future of \w+ is here|blazing[- ]fast|lightning[- ]fast|built for (the way )?(modern|today's) \w+)\b",
  "Stock phrase. Say the specific thing instead."),
 ("em-dash", "high", r"—",
  "The single most named tell in AI prose. Restructure the sentence."),
 ("aphoristic-cadence", "high",
  # "more than just a X" is the tell. "more than a clause" is ordinary English, and matching it
  # cost a true sentence in this plugin's own prose before the pattern required "just".
  r"\b(not just \w+[^.]{0,40}\bbut\b|it'?s not (about|just) [^.]{0,40}\bit'?s\b|more than just (a|an) \w+)",
  "The shape a model falls into when a sentence needs to sound important."),
 ("false-breadth", "high",
  r"\b(whether (you're|you are|your team is|it's) [^.]{3,60}\bor\b|for (teams|businesses|developers|users) of (all|any) (sizes?|kinds?)|from (startups|solo founders) to (enterprises|large organi[sz]ations))",
  "An audience defined so widely it names nobody."),
 ("transition-filler", "high",
  r"(^|\s)(moreover|furthermore|additionally|in conclusion|in summary|to summari[sz]e)\b|\b(it'?s|it is) worth noting that\b|\bin (today's|an era where|a world where)\b",
  "Connectives marking a connection the sentences do not have."),
 ("generic-cta", "medium",
  r"^\s*(#+\s*)?(\[)?(Get Started|Learn More|Sign Up Free|Try It Now|Get Started Today|Start Free Trial)\b",
  "The label should say what happens next."),
 ("hedged-claim", "medium",
  r"\b(is designed to (help|improve|reduce|increase|make|ensure|enable)|(may|might|could) (potentially|eventually|ultimately|significantly) \w+|aims to|seeks to|strives to|can help (you )?(reduce|improve|increase|save|boost))\b",
  "A claim with the claim hedged out of it."),
 ("copula-avoidance", "medium",
  r"\b(serves as|stands as|represents a|boasts (a|an|over|more than)|is poised to|utili[sz]es? (the power of|cutting-edge|advanced)|delivers (unparalleled|unmatched|best-in-class))\b",
  "Press-release verbs standing in for \"is\"."),
 ("significance-inflation", "medium",
  r"\b((pivotal|watershed|defining|landmark|seminal) (moment|shift|change)|mark(s|ing)? a new (era|chapter|age)|the (evolution|future) of \w+ (is|has) (here|arrived|begun))\b",
  "A routine thing described as history."),
 ("invented-metrics", "high",
  r"\b(99\.9{1,2}%|10x|2x|3x|5x)\s+(faster|uptime|more|fewer|better|productivity)\b|\b(10,000|50,000|100,000|1M|10M)\+?\s+(users|developers|teams|companies|customers)\b",
  "A number with no source is a liability, not just a tell."),
 ("placeholder-name", "medium",
  r"\b((Jane|John) (Doe|Smith)|Acme Corp|Acme\b|Lorem ipsum|Widget Co)\b",
  "Placeholder names say the page was never reviewed."),
 # residue, retargeted to markdown
 # The trailer branch used to be a bare `Co-Authored-By:`, which fired on a page whose only
 # sin was crediting a human colleague. A trailer is a tell when it names the tool, so it now
 # has to name one within the trailer's own line.
 ("agent-attribution", "high",
  r"\b(Generated (by|with)|Written by|Authored by|Created by)\s+(an?\s+)?(" + AI_NAMES + r")\b"
  r"|Co-Authored-By:[^\n]{0,60}\b(" + AI_NAMES + r")\b"
  r"|\bAs an AI\b",
  "A byline naming the tool. Outlives its accuracy the first time somebody edits underneath it."),
 ("conversation-artifact", "high",
  r"^\s*(Certainly|Of course|Sure thing|Great question|Happy to help|Let me know if|Here'?s (the|a|an|your) (updated|revised|corrected|new|complete)|I'?ve (added|updated|changed|refactored|implemented))\b",
  "Half of a conversation, committed. It addresses a reader who was not in it."),
 ("orchestration-marker", "high",
  r"\b(per|from|as required by) (the )?(phase|milestone|spec|plan|roadmap) (above|below|doc|\d)|\b(phase|milestone|casting|task|ticket|epic) #?\d+\b",
  "A note to the run that produced it. The next reader cannot look it up."),
 # markdown and docs specific
 ("emoji-heading", "high", r"^#{1,6}\s.*" + EMOJI,
  "Emoji in a heading is the loudest single markdown tell."),
 ("emoji-bullet", "medium", r"^\s*[-*]\s*" + EMOJI,
  "Emoji as a bullet icon. Use the words."),
 ("bold-label-bullets", "low", r"^\s*[-*]\s*\*\*[^*]{1,40}\*\*:",
  "Every bullet opening with a bold label is a template, not a list. Fine once, a tic by the fifth."),
 ("conclusion-section", "medium", r"^#{1,6}\s*(Conclusion|Summary|Final Thoughts|Wrapping Up|TL;?DR)\s*$",
  "A section that restates what the reader just read."),
 ("title-case-heading", "low",
  r"^#{1,6}\s+[A-Z][a-z]+(?:\s+(?:[A-Z][a-z]+|of|the|a|an|and|or|to|in|for|with|on)){2,}\s*$",
  "Sentence case for headings, not title case."),
 # diagrams
 ("diagram-rainbow", "medium",
  r"(fill|stroke|style)\s*[:=]?\s*#(f00|0f0|00f|ff0000|00ff00|0000ff)\b|classDef \w+ fill:#(f9f|bbf|bfb|fbb)\b",
  "Default mermaid palette. Pick colours that mean something or use none."),
 ("diagram-ai-purple", "high",
  r"#(6366f1|4f46e5|818cf8|7c3aed|6d28d9|8b5cf6|a855f7|9333ea)\b",
  "AI purple, the second most named visual tell."),
 ("diagram-vague-node", "medium",
  r"\[\s*(Data|Process|System|Service|Component|Module|Handler|Manager|Engine|Layer)\s*\]",
  "A node named after its category shows nothing. Name the actual thing."),
]

CASE_SENSITIVE = {"emoji-heading", "emoji-bullet", "title-case-heading",
                  "agent-attribution", "conversation-artifact", "placeholder-name"}

SKIP_FENCE = re.compile(r"^\s*```")


def unreadable(error):
    """os.walk's onerror. Re-raises instead of walking on.

    Left at its default, os.walk drops a directory it cannot list and keeps going, so an
    unreadable docs tree yields no files at all and main() prints the "no slop found across
    0 files" line a clean tree prints. Raising hands the error to main(), which has an exit
    code for a target it could not read.
    """
    raise error


def files():
    """Every markdown file under TARGETS. Raises OSError for a target that cannot be listed."""
    out = []
    for t in TARGETS:
        if os.path.isfile(t):
            out.append(t)
        else:
            for dp, dn, fn in os.walk(t, onerror=unreadable):
                dn[:] = [d for d in dn if not d.startswith(".") and d != "node_modules"]
                out += [os.path.join(dp, f) for f in fn if f.endswith((".md", ".mdx", ".mmd"))]
    return sorted(out)


def main():
    # A target that is not there walks to nothing, and "no slop found across 0 files" reads
    # exactly like a pass. drift.py:269, doctype.py:931 and scaffold.py:263 each return 2 for
    # a docs directory that is not there; this one returned 0, so a typo in the docs path
    # silently cleared the gate. (survey.py still returns 0 for a root that does not exist,
    # which is why this comment names three scripts and not "every other script".)
    missing = [t for t in TARGETS if not os.path.isfile(t) and not os.path.isdir(t)]
    if missing:
        for t in missing:
            print(f"no such target: {t}")
        return 2

    # Being there and being readable are two different questions, and the guard above only
    # asks the first. A *.md that is a dangling symlink is walked like any other file and
    # fails at open(); a directory nothing may list walks as if it were empty. Left alone the
    # first is a traceback, and a traceback exits 1 -- the code this script reserves for high
    # severity slop, now reported against a file nothing ever read -- while the second is a
    # silent exit 0. Both are the false pass the guard above exists to close, one step later.
    try:
        paths = files()
    except OSError as e:
        print(f"cannot read target: {e.filename} ({e.strerror or type(e).__name__})")
        return 2

    findings, tricolon_hits, bold_bullets = [], {}, {}
    for path in paths:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                # Read the page out before scanning it, so a read that fails part way
                # through is still a read failure and not a half-scanned file. `numbered`,
                # not `lines`: the frequency loops below already use that name.
                numbered = list(enumerate(f, 1))
        except OSError as e:
            print(f"cannot read target: {path} ({e.strerror or type(e).__name__})")
            return 2
        in_fence, is_diagram_fence = False, False
        for n, line in numbered:
            if SKIP_FENCE.match(line):
                in_fence = not in_fence
                is_diagram_fence = in_fence and bool(re.search(r"(mermaid|d2|dot)", line))
                continue
            for rid, sev, pat, why in RULES:
                diagram_rule = rid.startswith("diagram-")
                # prose rules skip code blocks; diagram rules only run inside diagram fences
                if diagram_rule and not is_diagram_fence:
                    continue
                if not diagram_rule and in_fence:
                    continue
                if re.search(pat, line, 0 if rid in CASE_SENSITIVE else re.I):
                    if rid == "bold-label-bullets":
                        bold_bullets.setdefault(path, []).append(n)
                        continue
                    findings.append((sev, path, n, rid, why, line.strip()[:90]))
            # tricolon: three comma-joined adjectives ending a sentence
            if not in_fence and re.search(r"\b\w+, \w+,? and \w+\.", line):
                tricolon_hits.setdefault(path, []).append(n)

    # frequency rules: one is a device, four is a tic
    for path, lines in tricolon_hits.items():
        if len(lines) >= 4:
            findings.append(("medium", path, lines[0], "tricolon-cadence",
                             f"Everything arrives in threes, {len(lines)} times in this file.",
                             "the reader hears the rhythm instead of the claim"))
    for path, lines in bold_bullets.items():
        if len(lines) >= 5:
            findings.append(("low", path, lines[0], "bold-label-bullets",
                             f"{len(lines)} bullets in this file open with a bold label.",
                             "a template, not a list"))

    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f[0]], f[1], f[2]))
    if not findings:
        print(f"no slop found across {len(paths)} files")
        return 0
    for sev, path, n, rid, why, ctx in findings:
        print(f"{path}:{n}  [{sev}] {rid}\n    {ctx}\n    {why}")
    high = sum(1 for f in findings if f[0] == "high")
    print(f"\n{len(findings)} findings, {high} high")
    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main())
