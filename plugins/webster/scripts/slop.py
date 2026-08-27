#!/usr/bin/env python3
"""Slop detector for documentation prose and diagrams.

Copy and residue rules retargeted from rendered HTML to markdown, plus tells specific to
documentation and to generated diagrams.

Prints findings as file:line. Exit 1 when anything at high severity fires.
"""
import os, re, sys

TARGETS = sys.argv[1:] or ["docs"]

# id, severity, pattern, why. Patterns run per line against markdown source.
# CASE_SENSITIVE rules are matched without re.I, because their whole point is capitalisation.
RULES = [
 ("marketing-buzzword", "high",
  r"\b(supercharge|unleash|revolutioni[sz]e|seamlessly|seamless|effortlessly|effortless|game-chang\w+|cutting-edge|next-level|10x your|unlock the power|elevate|empower|leverage|robust)\b",
  "Superlatives that survive a find and replace of the product name say nothing about the product."),
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
 ("agent-attribution", "high",
  r"\b(Generated (by|with)|Written by|Authored by|Created by)\s+(an?\s+)?(AI|LLM|Claude|GPT|Copilot|Cursor|Gemini|ChatGPT)\b|Co-Authored-By:|\bAs an AI\b",
  "A byline naming the tool. Outlives its accuracy the first time somebody edits underneath it."),
 ("conversation-artifact", "high",
  r"^\s*(Certainly|Of course|Sure thing|Great question|Happy to help|Let me know if|Here'?s (the|a|an|your) (updated|revised|corrected|new|complete)|I'?ve (added|updated|changed|refactored|implemented))\b",
  "Half of a conversation, committed. It addresses a reader who was not in it."),
 ("orchestration-marker", "high",
  r"\b(per|from|as required by) (the )?(phase|milestone|spec|plan|roadmap) (above|below|doc|\d)|\b(phase|milestone|casting|task|ticket|epic) #?\d+\b",
  "A note to the run that produced it. The next reader cannot look it up."),
 # markdown and docs specific
 ("emoji-heading", "high", r"^#{1,6}\s.*[\U0001F300-\U0001FAFF☀-➿]",
  "Emoji in a heading is the loudest single markdown tell."),
 ("emoji-bullet", "medium", r"^\s*[-*]\s*[\U0001F300-\U0001FAFF☀-➿]",
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


def files():
    out = []
    for t in TARGETS:
        if os.path.isfile(t):
            out.append(t)
        else:
            for dp, dn, fn in os.walk(t):
                dn[:] = [d for d in dn if not d.startswith(".") and d != "node_modules"]
                out += [os.path.join(dp, f) for f in fn if f.endswith((".md", ".mdx", ".mmd"))]
    return sorted(out)


def main():
    findings, tricolon_hits, bold_bullets = [], {}, {}
    for path in files():
        in_fence, is_diagram_fence = False, False
        with open(path, encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
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
        print(f"no slop found across {len(files())} files")
        return 0
    for sev, path, n, rid, why, ctx in findings:
        print(f"{path}:{n}  [{sev}] {rid}\n    {ctx}\n    {why}")
    high = sum(1 for f in findings if f[0] == "high")
    print(f"\n{len(findings)} findings, {high} high")
    return 1 if high else 0


if __name__ == "__main__":
    sys.exit(main())
