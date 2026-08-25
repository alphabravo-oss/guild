#!/usr/bin/env python3
"""Per-page content-type templates and quality checks.

Content types and their skeletons come from The Good Docs Project. The quality characteristics
come from ISO/IEC/IEEE 26514, and only the ones that can be measured mechanically are checked
here; the rest are reported as what a human reviewer should judge.

  types                 list the content types and what each is for
  template <type>       print the starting skeleton for one type
  check [docs]          check every page. exit 1 on a defect, 0 when only advisories remain

A template is a starting point for a blank page. It is not a validation rule: only 3 of
Harvester's 128 pages carry a literal "Overview" heading, so requiring one would enforce a
convention that real documentation does not follow. What is checked is what is actually a
defect, and the two are kept apart on purpose.
"""
import os, re, sys

# forbidden_shapes: content belonging to a different type. Mixing types is the failure Diataxis
# and Good Docs both name first, and it is detectable by shape rather than by vocabulary.
TYPES = {
 "tutorial": {
   "purpose": "Learning oriented. Hands on, teaches a skill. 15 to 60 minutes.",
   "sections": ["Overview", "Before you begin", "Steps", "Next steps"],
   "forbidden_shapes": ["option-table"],
   "advisories": ["learning-objective", "prerequisites"],
   "weights": {"comprehensive": "should not", "writing quality": "must",
               "currency": "should", "specific persona": "must"},
 },
 "how-to": {
   "purpose": "Problem oriented. A reader who knows the basics needs one task done.",
   "sections": ["Overview", "Before you start", "Steps", "See also"],
   "forbidden_shapes": [],
   "advisories": ["prerequisites"],
   "weights": {"comprehensive": "should not", "writing quality": "should",
               "currency": "must", "specific persona": "must"},
 },
 "reference": {
   "purpose": "Information oriented. Structured entries, looked up rather than read.",
   "sections": ["Overview", "See also"],
   "forbidden_shapes": ["procedure"],
   "advisories": ["structured-entries"],
   "weights": {"comprehensive": "must", "writing quality": "may",
               "currency": "must", "specific persona": "may"},
 },
 "explanation": {
   "purpose": "Understanding oriented. Context, background, why it is built this way.",
   "sections": ["Overview", "Where to next"],
   "forbidden_shapes": ["procedure", "endpoint-schema"],
   "advisories": [],
   "weights": {"comprehensive": "may", "writing quality": "must",
               "currency": "may", "specific persona": "should"},
 },
 "quickstart": {
   "purpose": "The primary feature, end to end, as fast as possible. Under two hours.",
   "sections": ["Scope", "Install", "Hello world", "Next steps"],
   "forbidden_shapes": ["option-table"],
   "advisories": [],
   "weights": {"comprehensive": "should not", "writing quality": "must",
               "currency": "must", "specific persona": "must"},
 },
 "api-reference": {
   "purpose": "Every endpoint, parameter and response. Generated where possible.",
   "sections": ["Overview", "Authentication", "Errors"],
   "forbidden_shapes": [],
   "advisories": ["structured-entries"],
   "weights": {"comprehensive": "must", "writing quality": "may",
               "currency": "must", "specific persona": "may"},
 },
 "glossary": {
   "purpose": "Terms a reader will not know, defined in their own words.",
   "sections": [],
   "forbidden_shapes": ["procedure"],
   "advisories": [],
   "weights": {"comprehensive": "should", "writing quality": "must",
               "currency": "should", "specific persona": "must"},
 },
 "troubleshooting": {
   "purpose": "A symptom the reader has, its cause, and the fix.",
   "sections": ["See also"],
   "forbidden_shapes": [],
   "advisories": ["symptom"],
   "weights": {"comprehensive": "should", "writing quality": "should",
               "currency": "must", "specific persona": "may"},
 },
}

# A numbered list is not a procedure. "1. A free automated scan" enumerates; "1. Open the app"
# instructs. The imperative verb is the discriminator, so the shape test looks for one.
IMPERATIVE = (r"(?:Run|Open|Click|Install|Create|Add|Set|Copy|Navigate|Select|Enter|Type|"
              r"Choose|Go|Download|Configure|Edit|Delete|Save|Press|Paste|Check|Verify|Launch|"
              r"Start|Stop|Restart|Apply|Deploy|Build|Clone|Import|Export|Upload|Log in|Sign in)")

SHAPES = {
 "procedure": (rf"^\s*(?:1\.|Step 1[:.]?)\s+{IMPERATIVE}\b", "step by step instructions"),
 "option-table": (r"^\|\s*(?:Name|Option|Flag|Parameter|Field|Argument)\s*\|", "an option table"),
 "endpoint-schema": (r"^\s*(?:GET|POST|PUT|PATCH|DELETE)\s+/\S", "endpoint schemas"),
}

# Who a page is written for. The grade ceiling follows from the audience rather than being one
# number for the whole site: a page for someone with no development background and a page for
# whoever operates the deployment cannot be held to the same sentence length.
AUDIENCES = {
 "user": {
   "who": "Uses the product. No development, sysadmin or DevOps background.",
   "grade": 10,
   "assumes": "general computer literacy and a real reason to be here",
 },
 "operator": {
   "who": "Installs, configures and runs the product. Comfortable with a terminal.",
   "grade": 13,
   "assumes": "a terminal, a package manager, environment variables, a hosting dashboard",
 },
 "developer": {
   "who": "Builds against the product or contributes to it.",
   "grade": 15,
   "assumes": "the language, the toolchain, and how to read source",
 },
}
DEFAULT_AUDIENCE = "user"

# ISO/IEC/IEEE 26514 quality characteristics. The four marked measurable are checked below.
ISO_26514 = {
 "usability": "a human judges whether a reader can find and apply the information",
 "clarity": "a human judges it",
 "accessibility": "measurable: alt text on images, heading levels not skipped",
 "correctness": "measurable elsewhere: drift.py resolves every cited anchor",
 "consistency": "measurable: one term for one thing",
 "understandability": "measurable: reading grade against the stated reader",
 "conciseness": "measurable: sentence length",
 "minimalism": "a human judges whether anything here is unnecessary",
}

SKELETON = {
 "tutorial": """## Overview

By the end of this tutorial you will be able to {verb} {thing}.

**Who this is for.** {the reader, named concretely}
**How long it takes.** {minutes}
**What you need to know already.** {or "nothing"}

## Before you begin

{Everything the reader must have: software, credentials, access. A reader who reaches step 4
and discovers a missing prerequisite has been failed by this section.}

## Steps

### 1. {Verb first}

{What to do, then the command, then what they should see.}

## Next steps

{Where to go now. Link, do not restate.}
""",
 "how-to": """## Overview

{The one problem this solves, in a sentence. Link to the explanation page for the concept
rather than teaching it here.}

## Before you start

{Prerequisites, with links to whatever they need first.}

## Steps

1. {Verb first.}

## See also

{Related but not required. If it is required it belongs in Before you start.}
""",
 "reference": """## Overview

{What every entry on this page has in common. One or two sentences.}

## {Subset of entries}

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| | | | |
""",
 "explanation": """## Overview

{What the reader will understand after this, and who they are.}

## {The idea, introduced gradually}

{Context first, then the idea, then why it was built this way and what was refused.
If you start writing steps, that is a how-to and belongs on its own page.}

## Where to next

{Links.}
""",
 "quickstart": """## Scope

{The core purpose of this tool, and the one minimal use case covered here.}

## Install

{The shortest path. Remove setup burden wherever possible.}

## Hello world

{The simplest end to end thing that proves it works.}

## Next steps

{What else exists. Links only.}
""",
 "api-reference": """## Overview

{What this API is for, and what every endpoint here has in common.}

## {Endpoint}

| Parameter | Type | Required | Description |
| --- | --- | --- | --- |
| | | | |
""",
 "glossary": """{One term per subsection, defined in the reader's words rather than the product's.
Judge the introduction of a term, not its existence: undefined domain vocabulary is jargon,
defined domain vocabulary is not.}

## {Term}

{Definition, then why the reader is meeting it here.}
""",
 "troubleshooting": """## {Symptom the reader can match against}

**What you see.** {the error, verbatim}
**Cause.** {why}
**Fix.** {what to do}
""",
}

FENCE = re.compile(r"^\s*```")

# A published page is a product surface, not a working note. These are the ways a documentation
# tool's own machinery leaks into what a reader sees.
SRC_EXT = ("ts|tsx|js|jsx|mjs|cjs|py|go|rs|rb|java|kt|swift|c|h|cc|cpp|cs|php|sh|bash|zsh"
           "|sql|css|scss|vue|svelte|astro|toml|ini|lock")
VISIBLE_ANCHOR = re.compile(rf"`[\w./-]+\.(?:{SRC_EXT}):\d+`")
VISIBLE_SRC_PATH = re.compile(rf"`(?:src|lib|internal|pkg|app|cmd)/[\w./-]+\.(?:{SRC_EXT})`")
WORKING_TAG = re.compile(r"(?:^|\s)\*{0,2}\[\?\]\*{0,2}(?:\s|$)|(?:^|\s)\[(?:SPEC|NOTE|BUG)\]")
AGENT_FILE = re.compile(r"`?(?:CLAUDE|AGENTS|GEMINI)\.md`?", re.I)


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


def prose_lines(text):
    """Lines outside code fences, which is where prose rules apply."""
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            out.append((n, line))
    return out


def syllables(word):
    word = word.lower().strip(".,;:!?()[]\"'`")
    if not word:
        return 0
    groups = re.findall(r"[aeiouy]+", word)
    n = len(groups)
    if word.endswith("e") and n > 1:
        n -= 1
    return max(1, n)


def reading_grade(text):
    """Flesch-Kincaid grade level, on prose only. ISO 26514 understandability."""
    words, sentences, sylls = 0, 0, 0
    for _, line in prose_lines(text):
        line = re.sub(r"`[^`]*`|\[([^\]]*)\]\([^)]*\)|[|#>*_-]", r"\1", line)
        if not line.strip():
            continue
        sentences += len(re.findall(r"[.!?](?:\s|$)", line))
        for w in re.findall(r"[A-Za-z][A-Za-z']+", line):
            words += 1
            sylls += syllables(w)
    if words < 100 or sentences == 0:
        return None
    return round(0.39 * (words / sentences) + 11.8 * (sylls / words) - 15.59, 1)


def check_universal(rel, text, audience, override):
    """Rules that hold for any page, typed or not, so documentation this plugin never wrote
    can still be audited."""
    defects, advisories = [], []

    # DEFECT: an image a screen reader cannot describe. ISO 26514 accessibility.
    for m in re.finditer(r"!\[([^\]]*)\]\(", text):
        if not m.group(1).strip():
            line = text[:m.start()].count("\n") + 1
            defects.append({"page": f"{rel}:{line}", "rule": "no-alt-text",
                            "problem": "image with empty alt text",
                            "fix": "describe what the image shows, for a reader who cannot see it"})

    # DEFECT: a skipped heading level. ISO 26514 accessibility.
    last = 0
    for n, line in prose_lines(text):
        m = re.match(r"^(#{1,6})\s+\S", line)
        if m:
            lvl = len(m.group(1))
            if last and lvl > last + 1:
                defects.append({"page": f"{rel}:{n}", "rule": "heading-skip",
                                "problem": f"h{last} followed by h{lvl}",
                                "fix": f"use h{last + 1}, or a screen reader reports a gap in the outline"})
            last = lvl

    # DEFECT: the implementation path a claim was checked against, shown to a reader. The
    # anchor belongs in an HTML comment where drift.py reads it, not in the prose.
    for pat, what, fix in (
        (VISIBLE_ANCHOR, "a file:line reference in the prose",
         "move it into an HTML comment beside the claim: <!-- src/foo.ts:12 -->"),
        (VISIBLE_SRC_PATH, "a source file path in the prose",
         "name the behaviour instead, or link to the file on the code host"),
        (WORKING_TAG, "a working-note tag ([?], [SPEC], [NOTE], [BUG])",
         "say the uncertainty in ordinary words, or cut the sentence"),
        (AGENT_FILE, "an internal agent instruction file",
         "these are internal to the repository and mean nothing to a reader"),
    ):
        for n, line in prose_lines(text):
            if pat.search(line):
                defects.append({"page": f"{rel}:{n}", "rule": "internals-leak",
                                "problem": f"{what}, on a page a reader sees",
                                "fix": fix})
                break

    # ADVISORY: reading grade against this page's own reader. ISO 26514 understandability.
    ceiling = override if override is not None else AUDIENCES[audience]["grade"]
    g = reading_grade(text)
    if g is not None and g > ceiling:
        advisories.append({"page": rel, "rule": "reading-grade",
                           "problem": f"reads at grade {g}, above {ceiling} for a '{audience}' page",
                           "fix": "shorten the longest sentences; the grade is driven by "
                                  "sentence length more than by vocabulary"})
    return defects, advisories


def check_typed(rel, text, dt, spec):
    """Rules that depend on the page declaring what it is."""
    defects, advisories = [], []
    body = "\n".join(l for _, l in prose_lines(text))

    # DEFECT: content belonging to another type. The failure Diataxis names first.
    for shape in spec["forbidden_shapes"]:
        pat, human = SHAPES[shape]
        if re.search(pat, body, re.M):
            defects.append({"page": rel, "rule": "type-mixing",
                            "problem": f"a {dt} page contains {human}",
                            "fix": "move it to its own page of the right type and link to it"})

    # ADVISORIES: real guidance, not universal practice, so they never fail a build.
    for a in spec["advisories"]:
        if a == "learning-objective" and not re.search(
                r"by the end of this \w+[^.]{0,60}you (?:will|'ll) be able to", body, re.I):
            advisories.append({"page": rel, "rule": "learning-objective",
                               "problem": "a tutorial with no stated outcome",
                               "fix": "open with 'By the end of this tutorial you will be able to ...'"})
        if a == "prerequisites" and re.search(rf"^\s*1\.\s+{IMPERATIVE}\b", body, re.M) and not re.search(
                r"(?im)^#+.*(prerequisite|before you|what you need|requirements)", body):
            advisories.append({"page": rel, "rule": "prerequisites",
                               "problem": "has steps but never says what the reader needs first",
                               "fix": "add a prerequisites section, or say plainly that nothing is needed"})
        if a == "structured-entries" and not re.search(r"^\s*\|", body, re.M):
            advisories.append({"page": rel, "rule": "structured-entries",
                               "problem": f"a {dt} page with no table",
                               "fix": "reference reads best as structured entries"})
        if a == "symptom" and not re.search(r"(?i)\b(symptom|what you see|error message)\b", body):
            advisories.append({"page": rel, "rule": "symptom",
                               "problem": "troubleshooting with no symptom to match against",
                               "fix": "lead with what the reader actually sees"})

    return defects, advisories


def run_check(docs, override):
    defects, advisories, untyped, unaudienced, typed, stubs = [], [], [], [], 0, 0
    for dirpath, dirnames, filenames in os.walk(docs):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, docs)
            text = open(path, encoding="utf-8", errors="replace").read()
            if "webster: not written yet" in text:
                stubs += 1
                continue

            fm = frontmatter(text)
            audience = fm.get("audience", DEFAULT_AUDIENCE)
            if audience not in AUDIENCES:
                defects.append({"page": rel, "rule": "unknown-audience",
                                "problem": f"audience '{audience}' is not a known reader",
                                "fix": "one of: " + ", ".join(AUDIENCES)})
                audience = DEFAULT_AUDIENCE
            if "audience" not in fm:
                unaudienced.append(rel)

            d, a = check_universal(rel, text, audience, override)
            defects += d
            advisories += a

            dt = fm.get("doc_type")
            if not dt:
                untyped.append(rel)
                continue
            if dt not in TYPES:
                defects.append({"page": rel, "rule": "unknown-type",
                                "problem": f"doc_type '{dt}' is not a known type",
                                "fix": "one of: " + ", ".join(sorted(TYPES))})
                continue
            typed += 1
            d, a = check_typed(rel, text, dt, TYPES[dt])
            defects += d
            advisories += a
    return defects, advisories, untyped, unaudienced, typed, stubs


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "check"

    if mode == "types":
        for t, s in TYPES.items():
            print(f"\n{t}\n  {s['purpose']}")
            print(f"  skeleton sections: {', '.join(s['sections']) or 'free form'}")
            print("  may not contain:   " +
                  (", ".join(SHAPES[f][1] for f in s["forbidden_shapes"]) or "no restriction"))
            print("  weighted toward:   " +
                  ", ".join(f"{k} {v}" for k, v in s["weights"].items()))
        print("\nAudiences, and the reading grade each implies:")
        for a, spec in AUDIENCES.items():
            print(f"  {a:10} grade {spec['grade']:<3} {spec['who']}")
            print(f"  {'':10} assumes {spec['assumes']}")
        print("\nISO/IEC/IEEE 26514 quality characteristics:")
        for k, v in ISO_26514.items():
            print(f"  {k:20} {v}")
        return 0

    if mode == "template":
        t = args[1] if len(args) > 1 else ""
        if t not in SKELETON:
            sys.exit("unknown type. one of: " + ", ".join(sorted(SKELETON)))
        print(SKELETON[t], end="")
        return 0

    docs = args[1] if len(args) > 1 else "docs"
    env = os.environ.get("WEBSTER_READING_GRADE")
    override = float(env) if env else None
    if not os.path.isdir(docs):
        print(f"no docs directory at {docs}")
        return 2
    defects, advisories, untyped, unaudienced, typed, stubs = run_check(docs, override)

    checked = typed + len(untyped)
    print(f"{checked} pages checked, {typed} against a declared type, "
          f"{stubs} stubs skipped")
    if defects:
        print(f"\nDEFECTS ({len(defects)})")
        for d in defects:
            print(f"  {d['page']}  [{d['rule']}] {d['problem']}\n      {d['fix']}")
    if advisories:
        print(f"\nADVISORIES ({len(advisories)})")
        for a in advisories:
            print(f"  {a['page']}  [{a['rule']}] {a['problem']}\n      {a['fix']}")
    if unaudienced:
        print(f"\nNO audience ({len(unaudienced)}), so each was read against the "
              f"'{DEFAULT_AUDIENCE}' default. Declare one when that is wrong.")
        for u in unaudienced[:12]:
            print(f"  {u}")
        if len(unaudienced) > 12:
            print(f"  ... and {len(unaudienced) - 12} more")
    if untyped:
        print(f"\nNO doc_type ({len(untyped)}). Accessibility and readability were still checked; "
              "the type rules were not.")
        for u in untyped[:12]:
            print(f"  {u}")
        if len(untyped) > 12:
            print(f"  ... and {len(untyped) - 12} more")
    if not defects and not advisories and not untyped and not unaudienced:
        print("every page matches its declared type")
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
