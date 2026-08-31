#!/usr/bin/env python3
"""Measure the shape of the prose on a page: what a reader has to hold in their head.

  check [docs]    measure every page. A `long-sentence` or a `dense-section` exits 1;
                  `long-paragraph`, `passive-voice`, `nominalisation` and `fragmented` alone
                  exit 0; no docs directory at the given path, or a tree of nothing but
                  stubs, exits 2
  limits          print the thresholds each audience is held to

This is a different question from the one `doctype.py` asks. That script judges a page against
what it declares: the right reader, the right type, the right subject matter, and a
Flesch-Kincaid grade against the reader's ceiling. Grade is a vocabulary-and-length statistic
and it is blind to shape, which is what actually decides whether a page can be read. Measured on
two pages held to the same ceiling of 10:

    "It is set by it. The setting of it is done by the system. It is then used by
     it for the doing of the thing."                                          grade -0.2, passed

    "Open the Deployments page and choose New deployment, then pick the cloud
     provider you already have a credential for."                           grade 10.2, flagged

The first is unreadable and scores well because it is monosyllabic. The second is the writing
`reader-lens` asks for and scores badly because naming the product costs syllables. Nothing here
uses syllables. It counts the things a reader actually pays for: a sentence they have to hold
open, a paragraph with no landing, a run of text with no heading to break it, and the two
constructions that reliably hide the actor.

Thresholds scale with the declared audience, on the same grounds the grade ceiling does: a page
for someone with no development background and a page for whoever operates the deployment cannot
be held to the same sentence.
"""
import os, re, sys

# Words, not lines: a threshold in lines measures the author's editor rather than the reader's
# effort, and reflows to nothing the moment somebody rewraps the file.
LIMITS = {
    "user":      {"sentence": 35, "paragraph": 90,  "section": 350},
    "operator":  {"sentence": 45, "paragraph": 120, "section": 450},
    "developer": {"sentence": 55, "paragraph": 150, "section": 600},
}
DEFAULT_AUDIENCE = "user"

# Ratios per hundred words, above which the construction has stopped being occasional. One
# threshold for every audience: a developer reading "the initialisation of the connection is
# performed by the handler" pays the same cost a user does.
PASSIVE_PER_100 = 3.0
NOMINAL_PER_100 = 3.0

FENCE = re.compile(r"^\s*```")
HEADING = re.compile(r"^(#{1,6})\s+(\S.*)$")
TABLE_ROW = re.compile(r"^\s*\|")

# "was created", "is being deployed", "have been removed". The participle test admits irregulars
# through the -en arm and accepts the common auxiliaries between copula and participle.
PASSIVE = re.compile(
    r"\b(?:is|are|was|were|be|been|being)\s+(?:\w+ly\s+)?(?:\w+ed|\w+en|done|made|shown|built|"
    r"held|kept|sent|set|put|read|written|given|taken|found|left|lost|meant|told)\b")

# A verb turned into a noun and propped up with a colourless one. Both spellings of the same
# evasion: "the creation of the cluster" and "the creating of the cluster".
NOMINAL = re.compile(
    r"\bthe\s+\w+(?:tion|sion|ment|ance|ence|ity|ness|ing)\s+of\b|"
    r"\b(?:perform|carry out|conduct|undertake|effect)(?:s|ed|ing)?\s+(?:a|an|the)\s+\w+")

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def frontmatter(text):
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        k, _, v = line.partition(":")
        if v:
            out[k.strip()] = v.strip().strip("\"'")
    return out


def prose_blocks(text):
    """(line_number, line) for every line, with anything that is not prose kept as None.

    Fenced code and table rows are dropped from the measurement and kept in the stream, because
    each ends the paragraph above it and starts a new one below. Removing the lines outright
    welded those two paragraphs into one and reported a length neither of them has.

    A table row is not prose and must not be measured as any: it contributes words and no
    sentence terminator, so a five-column table read as one sentence of 147 words. `doctype.py`
    excludes tables from the reading grade for the same reason, and a reader scans a table
    rather than holding it open the way they hold a sentence.
    """
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            out.append((n, None))
            continue
        out.append((n, None if fenced or TABLE_ROW.match(line) else line))
    return out


def words(s):
    return WORD.findall(s)


def sentences(block):
    """Sentence spans within one block of prose, as (offset_in_block, text).

    Split on terminal punctuation followed by space. A version number or an abbreviation can
    survive that split as its own fragment, which costs a short false sentence and never a long
    one, and this measure only asks about long ones.
    """
    out, pos = [], 0
    for part in SENTENCE_END.split(block):
        if part.strip():
            out.append((pos, part.strip()))
        pos += len(part) + 1
    return out


def paragraphs(lines):
    """Runs of consecutive prose lines, as (first_line_number, text).

    A list is one paragraph per item rather than one per run: a reader lands at every bullet, so
    a twelve-item list is not a wall the way twelve sentences of prose are.
    """
    out, buf, start, in_list = [], [], None, False
    def flush():
        nonlocal buf, start
        if buf:
            out.append((start, " ".join(buf)))
        buf, start = [], None
    for n, line in lines:
        if line is None or not line.strip() or HEADING.match(line or ""):
            flush()
            in_list = False
            continue
        item = re.match(r"^\s*(?:[-*+]|\d+\.)\s+", line)
        if item or (in_list and line.startswith((" ", "\t"))):
            flush()
            in_list = True
            out.append((n, line[item.end():] if item else line.strip()))
            continue
        in_list = False
        if start is None:
            start = n
        buf.append(line.strip())
    flush()
    return out


def sections(lines):
    """(heading_line, heading_text, depth, body_words) for each stretch of text under a heading.

    Text before the first heading is its own section, and a page with no heading at all is one
    section covering the page. Both are how a slab of unbroken text becomes visible: it is a
    section that ran long, not a special case needing its own rule.
    """
    out, cur, count = [], (0, "", 0), 0
    for n, line in lines:
        m = HEADING.match(line or "") if line is not None else None
        if m:
            out.append((*cur, count))
            cur, count = (n, m.group(2).strip(), len(m.group(1))), 0
            continue
        if line:
            count += len(words(line))
    out.append((*cur, count))
    # The lead-in before the first heading is a section only when something is actually there.
    return [s for s in out if s[0] or s[3]]


def per_100(pattern, text, total):
    return 0.0 if not total else len(pattern.findall(text)) * 100.0 / total


def measure(text, audience):
    limits = LIMITS.get(audience, LIMITS[DEFAULT_AUDIENCE])
    lines = prose_blocks(text)
    body = " ".join(l for _, l in lines if l and not HEADING.match(l))
    total = len(words(body))
    # Heading density asks whether there is enough under a heading to deserve one, and a table
    # is content the same way a paragraph is. Measuring it against prose alone reported every
    # reference page as fragmented the moment tables stopped being counted as prose.
    content = total + sum(len(words(l)) for l in text.splitlines() if TABLE_ROW.match(l))
    return {
        "audience": audience,
        "limits": limits,
        "words": total,
        "content_words": content,
        "paragraphs": paragraphs(lines),
        "sections": sections(lines),
        "passive": per_100(PASSIVE, body, total),
        "nominal": per_100(NOMINAL, body, total),
        "headings": sum(1 for _, l in lines if l and HEADING.match(l)),
    }


def check_page(rel, text, audience):
    m = measure(text, audience)
    limits, defects, advisories = m["limits"], [], []

    for line_no, para in m["paragraphs"]:
        for _, sent in sentences(para):
            n = len(words(sent))
            if n > limits["sentence"]:
                defects.append({
                    "page": f"{rel}:{line_no}", "rule": "long-sentence",
                    "problem": f"a {n}-word sentence, against {limits['sentence']} for a "
                               f"'{audience}' page",
                    "fix": f"split it. Starts: {' '.join(sent.split()[:9])} ...",
                })
        n = len(words(para))
        if n > limits["paragraph"]:
            advisories.append({
                "page": f"{rel}:{line_no}", "rule": "long-paragraph",
                "problem": f"{n} words with no break, against {limits['paragraph']}",
                "fix": "a reader scanning for the next thing to do has nowhere to land",
            })

    for line_no, title, _, body_words in m["sections"]:
        if body_words > limits["section"]:
            where = f"under '{title}'" if title else "before the first heading"
            defects.append({
                "page": f"{rel}:{line_no or 1}", "rule": "dense-section",
                "problem": f"{body_words} words {where} with no subheading, against "
                           f"{limits['section']}",
                "fix": "give the parts names, or move one of them to its own page",
            })

    if m["words"] >= 150:
        if m["passive"] > PASSIVE_PER_100:
            advisories.append({
                "page": rel, "rule": "passive-voice",
                "problem": f"{m['passive']:.1f} passive constructions per 100 words, against "
                           f"{PASSIVE_PER_100}",
                "fix": "name who does it. A reader following instructions needs the actor",
            })
        if m["nominal"] > NOMINAL_PER_100:
            advisories.append({
                "page": rel, "rule": "nominalisation",
                "problem": f"{m['nominal']:.1f} verbs turned into nouns per 100 words, against "
                           f"{NOMINAL_PER_100}",
                "fix": "'the creation of the cluster' is 'create the cluster'",
            })

    if m["headings"] >= 5 and m["content_words"] / m["headings"] < 40:
        advisories.append({
            "page": rel, "rule": "fragmented",
            "problem": f"{m['headings']} headings across {m['content_words']} words, one every "
                       f"{m['content_words'] // m['headings']}",
            "fix": "headings this close together are a list wearing an outline",
        })
    return defects, advisories


def run(docs):
    defects, advisories, pages = [], [], 0
    for dirpath, dirnames, filenames in os.walk(docs):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, docs)
            text = open(path, encoding="utf-8", errors="replace").read()
            if "webster: not written yet" in text:
                continue
            pages += 1
            d, a = check_page(rel, text, frontmatter(text).get("audience", DEFAULT_AUDIENCE))
            defects += d
            advisories += a
    return defects, advisories, pages


def report(label, items, limit):
    if not items:
        return
    by_rule = {}
    for i in items:
        by_rule.setdefault(i["rule"], []).append(i)
    print(f"\n{label} ({len(items)})")
    for rule in sorted(by_rule, key=lambda r: -len(by_rule[r])):
        group = by_rule[rule]
        print(f"\n  {rule}  ({len(group)})\n      {group[0]['fix']}")
        for i in group[:limit]:
            print(f"    {i['page']}  {i['problem']}")
        if len(group) > limit:
            print(f"    ... and {len(group) - limit} more")


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "check"

    if mode == "limits":
        print("Words. A sentence or section above its limit is a defect; a paragraph above it "
              "is an advisory.\n")
        print(f"  {'audience':11}{'sentence':>10}{'paragraph':>11}{'section':>9}")
        for a, l in LIMITS.items():
            print(f"  {a:11}{l['sentence']:>10}{l['paragraph']:>11}{l['section']:>9}")
        print(f"\nEvery audience: passive voice above {PASSIVE_PER_100} per 100 words and "
              f"nominalisation above\n{NOMINAL_PER_100} per 100 words are advisories, measured "
              f"on pages of 150 words or more.")
        return 0

    docs = args[1] if len(args) > 1 else "docs"
    if not os.path.isdir(docs):
        print(f"no docs directory at {docs}")
        return 2

    defects, advisories, pages = run(docs)
    if not pages:
        print(f"no written page under {docs}")
        return 2

    print(f"{pages} pages measured")
    limit = int(os.environ.get("WEBSTER_SHOW_PER_RULE", "6"))
    report("DEFECTS", defects, limit)
    report("ADVISORIES", advisories, limit)
    if not defects and not advisories:
        print("every page is shaped to be read")
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
