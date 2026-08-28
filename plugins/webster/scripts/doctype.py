#!/usr/bin/env python3
"""Per-page content-type templates and quality checks.

Content types and their skeletons come from The Good Docs Project. The quality characteristics
come from ISO/IEC/IEEE 26514, and only the ones that can be measured mechanically are checked
here; the rest are reported as what a human reviewer should judge.

  types                 list the content types, the readers, and what each is for
  template <type> [aud] print the starting skeleton, in the variant that reader needs
  check [docs]          check every page. exit 1 on a defect, 0 when only advisories remain

Every page declares two things: what it is (`doc_type`) and who it is for (`audience`). The
second one is a lens before it is a reading grade. A page can describe a status enum in short
words and still be about the wrong thing, so `audience` decides subject matter as well as
sentence length, and a page that declares no reader is a defect rather than a note.

A template is a starting point for a blank page. It is not a validation rule: only 3 of
Harvester's 128 pages carry a literal "Overview" heading, so requiring one would enforce a
convention that real documentation does not follow. What is checked is what is actually a
defect, and the two are kept apart on purpose.
"""
import json, os, re, sys

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

# The audience is a lens before it is a reading grade. "How hard are the sentences" and "what is
# this page allowed to be about" are different questions, and only the first one used to be
# asked here. A page can describe a status enum in short words and read at grade 9.
#
# A `user` page is written from the screen: what a person can see, click, type, and get back.
# The moment it names the machinery underneath, the reader is being shown the implementation
# instead of the product. `operator` may name the things an operator actually handles, and
# `developer` may name anything.
LENS_MAY_NOT = {
 "user": ("internal symbols, routes, environment variables and architecture",
          "write it from the screen: what the reader sees, clicks, types and gets back"),
 "operator": ("internal symbols and architecture",
              "an operator handles config and commands, not the code underneath them"),
 "developer": (None, None),
}

# Backticked identifiers that are a symbol in the source rather than something on screen.
#
# One capital run and the lowercase tail that follows it. Written once because every branch
# below needs it: it is what makes `APIClient` two words rather than one unreadable run, and
# what lets `GraphQL` end on an acronym. The old pattern required every segment to be
# [A-Z][a-z0-9]+, so a name that opened or closed on an acronym never matched: `APIClient` and
# `HTTPServer` passed the lens while `getUser` was reported, and 14 of the allowlist entries
# below were unreachable for the same reason.
_CAP_RUN = r"(?:[A-Z]+[a-z0-9]*)"
CODE_IDENT = re.compile(
 r"`("
 # snake_case. The dominant symbol shape in this repo's own scripts, and it used to pass.
 r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"
 rf"|[a-z][a-z0-9]*{_CAP_RUN}+"        # camelCase: getUser, iPhone, gRPC, iOS
 rf"|[A-Z][a-z0-9]+{_CAP_RUN}+"        # PascalCase: DataSources, GraphQL, PostgreSQL
 rf"|[A-Z]{{2,}}[a-z0-9]+{_CAP_RUN}*"  # acronym first: APIClient, HTTPServer, OAuth
 r")`")
# Tokens holding a `.` or a `/` are excluded by the closing backtick rather than by a rule of
# their own: no branch above can consume either character, so `my_config.yaml` and `/etc/hosts`
# never reach it. ALL-CAPS is excluded the same way — every branch requires a lowercase letter,
# which leaves `DATABASE_URL` to ENV_VAR, whose job it is.

# Product and technology names are PascalCase too, and they are not internals. Compared against
# `m.group(1).lower()`, so the capitalisation here is documentation rather than a matcher: the
# set used to hold `IPhone` and flag the `iPhone` a page actually writes.
IDENT_ALLOW = {name.lower() for name in (
 "JavaScript", "TypeScript", "PostgreSQL", "MySQL", "SQLite", "GitHub", "GitLab", "BitBucket",
 "MongoDB", "DynamoDB", "CloudFront", "CloudWatch", "OpenAPI", "OpenSSL", "OpenShift", "GraphQL",
 "PowerShell", "WordPress", "SharePoint", "OneDrive", "DevOps", "macOS", "iOS", "iPhone",
 "NodeJS", "NextJS", "VMware", "OpenStack", "OpenTelemetry", "SendGrid", "PagerDuty",
 # Names a page for someone who uses the product will write and mean the product, not a symbol.
 "iPad", "iCloud", "YouTube", "LinkedIn", "PayPal", "WhatsApp", "eBay", "OAuth", "OpenID",
 "WebSocket", "gRPC",
)}
ENV_VAR = re.compile(r"`([A-Z][A-Z0-9]*_[A-Z0-9_]+)`")
# Any backticked path, not just the two prefixes an API happens to use: `/dashboard` and
# `/settings/profile` are as much a request route as `/api/health`, and both used to pass.
# The lookahead drops a token whose last segment carries a file extension, because a page may
# legitimately show a reader `/etc/hosts.conf`. Letters only, so `/api/v1.0/users` stays a
# route. `/etc/hosts` has no extension and is the known gap; WEBSTER_LENS_ALLOW covers it.
ROUTE_PATH = re.compile(
 r"`(?![^`\s]*\.[A-Za-z]{1,6}`)(/[^`\s]+)`"
 r"|(?:^|\s)((?:GET|POST|PUT|PATCH|DELETE)\s+/\S+)")
# A flag is something typed at a terminal, and a page written from the screen has no terminal in
# it. Suppressed by WEBSTER_LENS_ALLOW or by the product's own commands and labels, because a
# command-line product's users really do type `--verbose`.
FLAG = re.compile(r"`(--?[a-zA-Z][\w-]*)`")

# Architecture vocabulary. Each of these names a part of the system the reader cannot touch.
# Kept deliberately tight: "repository" alone means a git repository to half of all readers and
# "interface" alone means the web interface, so neither is here.
ARCH_HARD = re.compile(
 r"(?i)\b(handlers?|middlewares?|controllers?|goroutines?|mutexe?s?|subclass(?:es)?|singletons?"
 r"|service layer|data layer|business logic|database schema|the codebase|code ?paths?"
 r"|call ?sites?|dependency injection|de-?serializ\w+|serializ\w+|the payload"
 r"|the enum|the struct|ORM)\b")
# One product's internals are another product's domain vocabulary. A Kubernetes tool's operators
# genuinely handle controllers, and calling that a leak would be wrong. WEBSTER_LENS_ALLOW takes
# a comma-separated list of terms this product's readers actually use, and it is a deliberate
# declaration rather than a default, because the easy way to defeat this rule is to widen it.
LENS_ALLOW = {t.strip().lower() for t in os.environ.get("WEBSTER_LENS_ALLOW", "").split(",")
              if t.strip()}


def load_survey_allow(path):
    """The product's own words, from a saved survey.py JSON. Returns (terms, loaded).

    survey.py already enumerates what a person sees — the screens, the labels printed on them,
    the commands they type — and none of it reached this lens, so a real product label like
    `DataSources` was reported as an internal symbol on every page that used it. The transport
    is a path in WEBSTER_SURVEY rather than an import or a subprocess, so the two scripts stay
    independent and the caller decides how fresh the survey is.

    Absent, missing or malformed is not an error: the survey is an optional courtesy and a check
    that refused to run without one would be worse than a check with a smaller allowlist. It
    also cannot print or raise, because scaffold.py imports this module for SKELETON and would
    get the noise on its own JSON stdout."""
    if not path:
        return set(), False
    try:
        with open(path, encoding="utf-8") as fh:
            user_surface = json.load(fh).get("user_surface") or {}
        terms = set()
        # labels carry `text`, screens and commands carry `name` (survey.py:236, 264, 311).
        for key, field in (("labels", "text"), ("screens", "name"), ("commands", "name")):
            for item in user_surface.get(key) or []:
                term = item.get(field) if isinstance(item, dict) else item
                if isinstance(term, str) and term.strip():
                    terms.add(term.strip().lower())
        return terms, True
    except Exception:
        return set(), False


SURVEY_PATH = os.environ.get("WEBSTER_SURVEY", "")
SURVEY_ALLOW, SURVEY_LOADED = load_survey_allow(SURVEY_PATH)
ARCH_SOFT = re.compile(
 r"(?i)\b(under the hood|behind the scenes|internally|the backend|the front ?end"
 r"|the database|the runtime)\b")

# An acronym a reader has not met is the most common way a page assumes knowledge of the product.
ACRONYM = re.compile(r"\b([A-Z][A-Z0-9]{1,5})s?\b")
# Acronyms nobody expands, because expanding them would read as condescension. Every entry has
# to be reachable by the regex above — 2 to 6 characters — or it is a promise the check does not
# keep. Four entries were not, and the last real run reported the words they were meant to
# excuse.
UNIVERSAL_ACRONYMS = {
 "US", "UK", "EU", "ID", "OK", "FAQ", "AM", "PM", "USB", "PDF", "CSV", "URL", "HTTP", "HTTPS",
 "HTML", "CSS", "JSON", "YAML", "XML", "ZIP", "GPS", "SMS", "PC", "TV", "IT", "AI", "OS", "RAM",
 "CPU", "GPU", "GB", "MB", "KB", "TB", "UTC", "ISO", "PIN", "QR", "SIM", "WIFI", "MD",
 "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD",
 "README", "TODO", "NOTE", "TIP", "YES", "NO", "AND", "OR", "NOT", "ALL",
 "NEW", "END", "MAX", "MIN",
 # A page for someone who uses the product still says API and CLI, and expanding either one
 # reads as condescension rather than help. They were the two the brief named.
 "API", "CLI",
 # The rest of the same class: an interface, a connection or an address a reader meets by these
 # letters and never by the words behind them.
 "UI", "UX", "SDK", "SSH", "SSL", "TLS", "VPN", "DNS", "IP",
 # Removed: "FAQ" and "OK" were each listed twice, so one copy of each is gone.
 # Removed: "N", "A", "I" — ACRONYM needs two characters, so none of them could ever match.
 # Removed: "WARNING" — seven characters against a six-character ceiling, so it was allowlisted
 # and reported at the same time. A-034 keeps the ceiling and drops the entry.
}

# The plan this plugin writes for itself is a working document that happens to live in the docs
# directory. Auditing it as a page reports its own working notes as defects on a published page.
NOT_A_PAGE = {"docs-plan.md", "llms.txt", "README.md"}
SECOND_PERSON = re.compile(r"(?i)\byou\b|\byour\b|\byours\b")
# Products put their own labels in capitals, and quoting one is the opposite of jargon: it is
# the page using the reader's own screen. "a group labeled HABITAT" is not an unexpanded acronym.
# The cue has to be near the word it excuses. Applied to a whole line it suppressed every
# acronym on any line containing a verb like "reads", which is most of them.
ON_SCREEN_CUE = re.compile(
 r"(?i)\b(labell?ed|headed|titled|button|tab|menu|section|column|field|heading|panel|option"
 r"|marked|named|called|reads|says|shows)\b[^.]{0,30}$")
CAPS_RUN = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b(?:\s*(?:&|and|/)?\s*\b[A-Z][A-Z0-9]{1,}\b)+")


# ISO/IEC/IEEE 26514 quality characteristics. The four marked measurable are checked below.
ISO_26514 = {
 "usability": "a human judges whether a reader can find and apply the information",
 "clarity": "a human judges it",
 "accessibility": "measurable: alt text on images, heading levels not skipped",
 "correctness": "measurable elsewhere: drift.py resolves every cited anchor",
 "consistency": "measurable: one term for one thing",
 "subject-fit": "measurable: whether the page is about something its reader can act on",
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
 "explanation@user": """## The choice you are making

{The decision this page settles, in the reader's words. An explanation written for someone who
uses the product earns its place by helping them choose. If there is no choice here, the page
is for a developer and belongs in developer/.}

## {What is actually different between the options}

{What the reader would see happen either way. Not the mechanism that produces it.}

## How to decide

{The question to ask themselves, and what each answer points at.}

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


def prose_lines(text, tables=True):
    """Lines outside code fences, which is where prose rules apply.

    tables=False also drops table rows. A table cell contributes words but no sentence
    terminator, so counting tables as prose inflates words-per-sentence in proportion to how
    much of a page is tabular. A reference page of 80% tables scored above 40 on a reading
    grade whose prose read at 9."""
    out, fenced = [], False
    for n, line in enumerate(text.splitlines(), 1):
        if FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        if not tables and line.lstrip().startswith("|"):
            continue
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
    for _, line in prose_lines(text, tables=False):
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


def assigned_env_vars(docs):
    """Which SCREAMING_SNAKE names are actually environment variables.

    `CREDENTIAL_ERROR` is a status the interface displays and `PIONEER_API_URL` is a variable
    somebody exports, and the two are indistinguishable by shape. So a name counts as a variable
    only where the documentation shows it being set somewhere."""
    found = set()
    for dirpath, dirnames, filenames in os.walk(docs):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            text = open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace").read()
            for m in re.finditer(r"(?:^|\s|`)(?:export\s+)?([A-Z][A-Z0-9]*_[A-Z0-9_]+)\s*=", text):
                found.add(m.group(1))
            for m in re.finditer(r"(?i)(?:variable|env(?:ironment)?)\b[^\n]{0,40}?`([A-Z][A-Z0-9]*_[A-Z0-9_]+)`", text):
                found.add(m.group(1))
    return found


def glossary_terms(docs):
    """Every term the documentation set defines somewhere, so a page that uses one is not
    accused of assuming it. A glossary page defines its headings; any page defines a term it
    expands in parentheses."""
    terms = set()
    for dirpath, dirnames, filenames in os.walk(docs):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            text = open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace").read()
            is_glossary = frontmatter(text).get("doc_type") == "glossary"
            for n, line in prose_lines(text):
                if is_glossary:
                    m = re.match(r"^#{2,6}\s+(.+?)\s*$", line)
                    if m:
                        terms.add(m.group(1).strip("`*").upper())
                        for a in ACRONYM.findall(m.group(1)):
                            terms.add(a)
                for a in re.findall(r"\(([A-Z][A-Z0-9]{1,5})s?\)", line):
                    terms.add(a)
    return terms


def expanded_on_page(acr, body):
    """The page itself introduces the acronym, either as 'network security group (NSG)' or as
    'NSG (network security group)'. Anything else is the reader being assumed to know it."""
    if re.search(rf"(?:[A-Za-z][\w-]*\s+){{1,6}}\({acr}s?\)", body):
        return True
    if re.search(rf"\b{acr}s?\b\s*\([^)]{{4,90}}\)", body):
        return True
    return False


def check_lens(rel, text, audience, dt, env_vars):
    """What the page is allowed to be ABOUT. The reading grade asks how hard the sentences are;
    this asks whether the subject matter belongs to this reader at all.

    A `user` page that names a symbol, a route or an environment variable is describing the
    machinery rather than the product. That is the failure the reading grade cannot see, because
    a short sentence about a status enum still reads at grade 9."""
    defects, advisories = [], []
    forbids, remedy = LENS_MAY_NOT.get(audience, (None, None))
    if forbids is None:
        return defects, advisories

    named = []
    for n, line in prose_lines(text):
        for m in CODE_IDENT.finditer(line):
            if m.group(1).lower() not in IDENT_ALLOW:
                named.append((n, f"`{m.group(1)}`", "an internal symbol name"))
        for m in ENV_VAR.finditer(line):
            if audience == "user" and m.group(1) in env_vars:
                named.append((n, f"`{m.group(1)}`", "an environment variable"))
        # Routes and flags belong to whoever is holding a terminal, and LENS_MAY_NOT says an
        # operator is. Running these two on an operator page reported it for naming
        # `/api/health` on a page that is allowed to be about exactly that, and widening
        # ROUTE_PATH would have multiplied the false finding. Symbols and architecture below
        # keep firing for an operator, which is what LENS_MAY_NOT['operator'] actually forbids.
        if audience == "user":
            for m in ROUTE_PATH.finditer(line):
                named.append((n, m.group(1) or m.group(2), "a request route"))
            for m in FLAG.finditer(line):
                named.append((n, f"`{m.group(1)}`", "a command-line flag"))
        for m in ARCH_HARD.finditer(line):
            named.append((n, m.group(1), "part of the architecture"))

    seen = set()
    for n, what, kind in named:
        term = what.strip("`").lower()
        # Two allowlists, kept apart on purpose. LENS_ALLOW is a person declaring that their
        # readers use this word; SURVEY_ALLOW is the product's own screens, labels and commands,
        # read from a survey rather than declared. Either one excuses the term.
        if term in LENS_ALLOW or term in SURVEY_ALLOW:
            continue
        if what.lower() in seen:
            continue
        seen.add(what.lower())
        defects.append({"page": f"{rel}:{n}", "rule": "wrong-lens",
                        "problem": f"a '{audience}' page names {kind}, {what}",
                        "fix": remedy})

    if audience == "user":
        soft = set()
        for n, line in prose_lines(text):
            for m in ARCH_SOFT.finditer(line):
                if m.group(1).lower() not in soft:
                    soft.add(m.group(1).lower())
                    advisories.append({"page": f"{rel}:{n}", "rule": "lens-drift",
                                       "problem": f"'{m.group(1)}' points at the machinery",
                                       "fix": "say what the reader sees happen instead"})

    # An explanation page for someone who uses the product exists to help them make a decision
    # they actually face. One that never addresses them is explaining a mechanism they cannot
    # touch, which is a developer page wearing the wrong frontmatter.
    if audience == "user" and dt == "explanation":
        body = "\n".join(l for _, l in prose_lines(text, tables=False))
        words = len(re.findall(r"[A-Za-z][A-Za-z']+", body))
        hits = len(SECOND_PERSON.findall(body))
        if words >= 200 and hits * 100.0 / words < 0.5:
            defects.append({"page": rel, "rule": "explains-mechanism",
                            "problem": f"a 'user' explanation page that addresses the reader "
                                       f"{hits} times in {words} words",
                            "fix": "an explanation for a user exists to settle a choice they "
                                   "face. If it explains something they cannot act on, it is a "
                                   "developer page"})
    return defects, advisories


def check_jargon(rel, text, audience, known):
    """A term used before the reader has met it. This is the mechanical half of the Readable
    gate, which otherwise depends on a reviewer being available."""
    findings = []
    body = "\n".join(l for _, l in prose_lines(text))
    linked_glossary = "glossary" in text.lower()
    seen = set()
    for n, line in prose_lines(text):
        if re.match(r"^\s*#{1,6}\s", line):
            continue
        line = re.sub(r"`[^`]*`|\*\*[^*]*\*\*|\[[^\]]*\]\([^)]*\)", "", line)
        runs = [r.span() for r in CAPS_RUN.finditer(line)]
        for m in ACRONYM.finditer(line):
            a = m.group(1)
            if a in UNIVERSAL_ACRONYMS or a in known or a in seen:
                continue
            if ON_SCREEN_CUE.search(line[:m.start()]):
                continue
            if any(x <= m.start() < y for x, y in runs):
                continue
            if expanded_on_page(a, body):
                continue
            seen.add(a)
            findings.append({"page": f"{rel}:{n}", "rule": "undefined-jargon",
                             "problem": f"'{a}' is used without ever being expanded",
                             "fix": f"write it out the first time, '... ({a})', or add it to "
                                    f"the glossary"})
    if linked_glossary:
        for f in findings:
            f["fix"] += "; the page links the glossary but this term is not in it"
    if audience == "user":
        return findings, []
    return [], findings


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


def check_frontmatter(rel, fm):
    """The three checks that need the frontmatter block and nothing else.

    Separated out so a stub can have them run on it. A page nobody has written yet still has to
    say who it is for and what it is: an all-stub tree used to pass the Shaped and Lens gates
    without one page having declared a reader, because every check skipped the stub entirely.
    The prose checks stay off — a skeleton's placeholder braces are not sentences, and grading
    them teaches the reader to ignore the report."""
    defects = []
    audience = fm.get("audience", DEFAULT_AUDIENCE)
    if audience not in AUDIENCES:
        defects.append({"page": rel, "rule": "unknown-audience",
                        "problem": f"audience '{audience}' is not a known reader",
                        "fix": "one of: " + ", ".join(AUDIENCES)})
        audience = DEFAULT_AUDIENCE
    # A page with no declared reader was, until this was a defect, read against the
    # `user` default and reported as a note nobody acted on. Every page in the last
    # real run of this plugin was in that state, so the lens never fired once.
    if "audience" not in fm:
        defects.append({"page": rel, "rule": "no-audience",
                        "problem": "the page does not say who it is for",
                        "fix": "add 'audience: user', 'operator' or 'developer'. It "
                               "decides what the page may be about, not just how hard "
                               "the sentences may be"})
    dt = fm.get("doc_type")
    if dt and dt not in TYPES:
        defects.append({"page": rel, "rule": "unknown-type",
                        "problem": f"doc_type '{dt}' is not a known type",
                        "fix": "one of: " + ", ".join(sorted(TYPES))})
    return defects, audience


def survey_line():
    """What the lens allowed beyond its own static list, said out loud.

    A silently smaller allowlist is the failure mode of an optional input: the run looks the
    same whether the survey was read or the path was a typo, and the reader is left to guess
    why their product's own labels are being reported as internals."""
    if not SURVEY_PATH:
        return "no survey allowlist"
    if not SURVEY_LOADED:
        return f"no survey allowlist: WEBSTER_SURVEY {SURVEY_PATH} could not be read"
    return (f"lens allowlist: {len(SURVEY_ALLOW)} terms from WEBSTER_SURVEY "
            f"({SURVEY_PATH})")


def run_check(docs, override):
    defects, advisories, untyped, typed, stubs, pages = [], [], [], 0, 0, 0
    known = glossary_terms(docs)
    env_vars = assigned_env_vars(docs)
    for dirpath, dirnames, filenames in os.walk(docs):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, docs)
            if rel in NOT_A_PAGE:
                continue
            text = open(path, encoding="utf-8", errors="replace").read()
            fm = frontmatter(text)
            defects_fm, audience = check_frontmatter(rel, fm)
            defects += defects_fm
            dt = fm.get("doc_type")

            # The frontmatter checks above have already run on this stub. Nothing below is
            # about a page that has not been written yet.
            if "webster: not written yet" in text:
                stubs += 1
                continue

            pages += 1
            d, a = check_universal(rel, text, audience, override)
            defects += d
            advisories += a
            d, a = check_lens(rel, text, audience, dt, env_vars)
            defects += d
            advisories += a
            d, a = check_jargon(rel, text, audience, known)
            defects += d
            advisories += a

            if not dt:
                untyped.append(rel)
                continue
            if dt not in TYPES:
                continue  # check_frontmatter already reported it
            typed += 1
            d, a = check_typed(rel, text, dt, TYPES[dt])
            defects += d
            advisories += a
    return defects, advisories, untyped, typed, stubs, pages


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
        print("\nAudiences. The audience is a lens first and a reading grade second:")
        for a, spec in AUDIENCES.items():
            print(f"  {a:10} grade {spec['grade']:<3} {spec['who']}")
            print(f"  {'':10} assumes {spec['assumes']}")
            forbids = LENS_MAY_NOT[a][0]
            print(f"  {'':10} " + (f"may not name {forbids}" if forbids
                                   else "may name anything in the system"))
        print("\nISO/IEC/IEEE 26514 quality characteristics:")
        for k, v in ISO_26514.items():
            print(f"  {k:20} {v}")
        return 0

    if mode == "template":
        t = args[1] if len(args) > 1 else ""
        aud = args[2] if len(args) > 2 else ""
        if f"{t}@{aud}" in SKELETON:
            t = f"{t}@{aud}"
        if t not in SKELETON:
            sys.exit("unknown type. one of: " +
                     ", ".join(sorted(k for k in SKELETON if "@" not in k)))
        print(SKELETON[t], end="")
        return 0

    docs = args[1] if len(args) > 1 else "docs"
    env = os.environ.get("WEBSTER_READING_GRADE")
    override = float(env) if env else None
    if not os.path.isdir(docs):
        print(f"no docs directory at {docs}")
        return 2
    defects, advisories, untyped, typed, stubs, pages = run_check(docs, override)

    # `pages` counts what was actually read as writing. The count used to be derived as
    # typed + untyped, which silently dropped every unknown-type page, and it is now the same
    # number the all-stub gate at the bottom asks about — a run cannot report "0 pages checked"
    # and then decide there was something to check.
    print(f"{pages} pages checked, {typed} against a declared type, "
          f"{stubs} stubs (frontmatter only)")
    print(survey_line())
    limit = int(os.environ.get("WEBSTER_SHOW_PER_RULE", "6"))
    for label, items in (("DEFECTS", defects), ("ADVISORIES", advisories)):
        if not items:
            continue
        by_rule = {}
        for i in items:
            by_rule.setdefault(i["rule"], []).append(i)
        print(f"\n{label} ({len(items)})")
        for rule in sorted(by_rule, key=lambda r: -len(by_rule[r])):
            group = by_rule[rule]
            print(f"\n  {rule}  ({len(group)})")
            print(f"      {group[0]['fix']}")
            for i in group[:limit]:
                print(f"    {i['page']}  {i['problem']}")
            if len(group) > limit:
                print(f"    ... and {len(group) - limit} more")
    if untyped:
        print(f"\nNO doc_type ({len(untyped)}). Accessibility and readability were still checked; "
              "the type rules were not.")
        for u in untyped[:12]:
            print(f"  {u}")
        if len(untyped) > 12:
            print(f"  ... and {len(untyped) - 12} more")
    # A tree of nothing but stubs resolved every rule it had, which is not the same as being
    # checked: it printed "every page matches its declared type" and exited 0 over pages that
    # were skeletons. This mirrors drift.py's no_anchors. A frontmatter defect on a stub is a
    # real finding, so it wins over not-checked the way a broken anchor does there.
    if stubs and not pages and not defects:
        print(f"{stubs} stubs, nothing to check")
        return 2
    if not defects and not advisories and not untyped:
        print("every page matches its declared type")
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
