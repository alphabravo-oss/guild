#!/usr/bin/env python3
"""Per-page content-type templates and quality checks.

Content types and their skeletons come from The Good Docs Project. The quality characteristics
come from ISO/IEC/IEEE 26514, and ISO_26514 below records how each of the nine is judged. Being
marked measurable there is not the same as being checked here: accessibility, understandability
and subject-fit each have a rule in this file, correctness is drift.py's, conciseness is
measured only as the sentence length feeding the grade, and consistency has no check here at
all. The remaining three are marked for a human reviewer to judge.

  types                 list the content types, the readers, and what each is for
  template <type> [aud] print the starting skeleton, in the variant that reader needs
  check [docs]          check every written page, and every stub for its frontmatter alone.
                        exit 1 on a defect, 0 when only advisories remain, 2 when there was
                        nothing to check: no docs directory, or no page that is not a stub
                        and no frontmatter defect on the stubs either

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
#
# The first string is the whole printed contract: `doctype.py types` prints it as "may not name
# ...", and it is the only place the tool tells a writer what the lens forbids before the lens
# fires. It said symbols, routes, environment variables and architecture while check_lens had
# already grown the flag rule and widened routes past the /api and /v<n> prefixes, so a writer
# read the contract, wrote `--verbose` on a user page, and got a defect for a class the tool had
# just told them was allowed. Every kind check_lens can report has to be named here, and only
# for the audience it fires on: flags and routes stay off the operator line because GI-003 keeps
# them off the operator page.
LENS_MAY_NOT = {
 "user": ("internal symbols, any route path, command-line flags, environment variables and "
          "architecture",
          "write it from the screen: what the reader sees, clicks, types and gets back"),
 "operator": ("internal symbols and architecture",
              "an operator handles config and commands, not the code underneath them"),
 "developer": (None, None),
}

# Backticked identifiers that are a symbol in the source rather than something on screen.
#
# One capital run and the lowercase tail that follows it. Written once because the three
# capital-run branches below share it — the snake_case branch names no capital and does not —
# and it is what makes `APIClient` two words rather than one unreadable run, and what lets
# `GraphQL` end on an acronym. The old pattern required every segment to be
# [A-Z][a-z0-9]+, so a name that opened or closed on an acronym never matched: `APIClient` and
# `HTTPServer` passed the lens while `getUser` was reported, and 14 of the 31 entries the
# allowlist held then were unreachable for the same reason.
_CAP_RUN = r"(?:[A-Z]+[a-z0-9]*)"
# One lowercase letter somewhere in the token, asserted once for the whole alternation. The two
# capital-initial branches below let a digit stand in for the lowercase tail — `HTTP2` is
# [A-Z]{2,} then [a-z0-9]+, `H2O` is [A-Z] then [a-z0-9]+ then a capital run — so widening them
# to reach `APIClient` also reported `SHA256`, `MD5`, `EC2` and `UTF8` as internal symbols on a
# user page. Those two are the only branches this lookahead is load-bearing for: the other two
# open on a mandatory [a-z] and carry a lowercase letter by construction. Requiring it here
# rather than inside those two keeps `S3Bucket`, which a per-branch [a-z] would have dropped.
# The class cannot cross the closing backtick, so the lookahead reads this token and nothing
# after it.
_HAS_LOWER = r"(?=[A-Za-z0-9_]*[a-z])"
CODE_IDENT = re.compile(
 rf"`{_HAS_LOWER}("
 # snake_case. The dominant symbol shape in this repo's own scripts, and it used to pass.
 r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"
 rf"|[a-z][a-z0-9]*{_CAP_RUN}+"        # camelCase: getUser, iPhone, gRPC, iOS
 rf"|[A-Z][a-z0-9]+{_CAP_RUN}+"        # PascalCase: DataSources, GraphQL, PostgreSQL
 rf"|[A-Z]{{2,}}[a-z0-9]+{_CAP_RUN}*"  # acronym first: APIClient, HTTPServer, OAuth
 r")`")
# Tokens holding a `.` or a `/` are excluded by the closing backtick rather than by a rule of
# their own: no branch above can consume either character, so `my_config.yaml` and `/etc/hosts`
# never reach it. ALL-CAPS splits in two, and only one half is the lookahead's doing.
# `DATABASE_URL` and `XML` match no branch with the lookahead or without it: the two
# capital-initial branches demand a lowercase-or-digit tail straight after the opening capitals
# and neither token has one, and the other two open on a mandatory [a-z]. `DATABASE_URL` goes
# on to ENV_VAR, the owner FR-010 names for ALL-CAPS ("ENV_VAR's job"), and check_lens reports
# it once assigned_env_vars has seen the docs set assign that name or call it a variable.
# `SHA256`, `MD5`, `EC2`, `UTF8` and `HTTP2` are the shapes _HAS_LOWER is for: drop the
# lookahead and every one of them matches the acronym-first branch, which is what a5484ba did
# and ec6aa60 undid. Nothing owns them now. check_jargon deletes every backticked span before
# ACRONYM runs, so a backticked `SHA256` reaches no rule at all, while the same word bare is
# reported as undefined jargon. That is a gap rather than a design: FR-010 hands ALL-CAPS to
# ENV_VAR and names no second owner, ENV_VAR requires an underscore, and check_jargon has
# deleted backticked spans since it was written, so a5484ba is the only revision of this file
# that ever reported one — and it reported it as an internal symbol, not as an acronym.

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
 # A version digit makes a different string, and the compare is exact, so allowing `OAuth`
 # did nothing for the `OAuth2` a page actually writes, and `IPv4` was never here at all.
 # Both were reported as internal symbols on a user page naming a public standard.
 # HTTP2 and HTTP3 are deliberately not here: they hold no lowercase letter, so CODE_IDENT
 # cannot produce them and an entry would be the unreachable kind this file has been
 # removing. An unexpanded HTTP2 is the acronym check's to judge, not this one's.
 "IPv4", "IPv6", "OAuth1", "OAuth2",
)}
ENV_VAR = re.compile(r"`([A-Z][A-Z0-9]*_[A-Z0-9_]+)`")
# Any backticked path, not just the two prefixes an API happens to use: `/dashboard` and
# `/settings/profile` are as much a request route as `/api/health`, and both used to pass.
# The lookahead drops a token whose last segment carries an extension, because a page may
# legitimately show a reader a file. It reads a dot followed by up to sixteen alphanumerics and
# then the closing backtick, so the dot it finds is always in the last segment.
# The bound used to be six letters, which covers `.conf` and `.yaml` and stops there: an
# ordinary config path on a user page — `/etc/app.properties`, `/docs/readme.markdown`,
# `/.gitignore`, `/x.template` — was over the length or held a digit, and every one of them was
# reported to a reader as naming a request route. Sixteen is past every extension this comment
# names and every extension on a path in tests/test_doctype.py, the longest of them
# `.properties` at ten letters, and the comment above this line claimed the exclusion without
# ever saying it was capped.
# Admitting digits so `.7z` and `.mp4` keep reading as files took the version paths with them:
# `.0` matched as an extension, so `/api/v1.0` and `/v3.0` went quiet — two routes the narrow
# /api and /v<n> rule this widening replaced had always reported, which made the fix a
# regression for the shape it was built from. The inner lookahead asks the extension for one
# letter somewhere, which `.0` has none of. What that costs is a last segment whose dot is
# followed by digits alone: `/data/backup.2024` and `/x.123` read as routes, and
# WEBSTER_LENS_ALLOW is the answer there, as it is for `/etc/hosts`, which has no extension at
# all and is the gap A-008 accepts.
# The first segment may not be empty. `[^`\s]+` let the path body open on a second slash, so
# `//` was matched as a path with no segment in it when A-008 asks for at least one, and a page
# writing `//` is showing a reader a comment marker or an empty root, not a route.
ROUTE_PATH = re.compile(
 r"`(?![^`\s]*\.(?=[A-Za-z0-9]{0,15}[A-Za-z])[A-Za-z0-9]{1,16}`)(/[^`\s/]+(?:/[^`\s]*)?)`"
 r"|(?:^|\s)((?:GET|POST|PUT|PATCH|DELETE)\s+/\S+)")
# A flag is something typed at a terminal, and a page written from the screen has no terminal in
# it. Suppressed by WEBSTER_LENS_ALLOW or by the product's own commands and labels, because a
# command-line product's users really do type `--verbose`.
FLAG = re.compile(r"`(--?[a-zA-Z][\w-]*)`")

# Architecture vocabulary. Each of these names a part of the system the reader cannot touch.
# Kept deliberately tight: "repository" alone can mean a git repository and "interface" alone
# can mean the web interface, either of which a user page may legitimately be about, so neither
# is here.
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
    terms = set()

    def add(term):
        """One term, plus the form a page would backtick it as."""
        if not isinstance(term, str) or not term.strip():
            return
        term = term.strip().lower()
        terms.add(term)
        # A screen name may arrive spaced — "Data Sources" — where a page backticks the same
        # screen closed up, `DataSources`, and a spaced term can never equal a backticked
        # token, so the whitespace-stripped spelling of the same name is allowed too. One
        # source, two spellings; not a fourth source.
        terms.add("".join(term.split()))

    try:
        with open(path, encoding="utf-8") as fh:
            user_surface = json.load(fh).get("user_surface") or {}
        # The shape survey.py writes, stated by field and by field only: user_surface.labels[]
        # carry `text`, user_surface.commands[] carry `name`, and user_surface.screens[] carry
        # `path` always and `name` only sometimes, spaced or not. Field paths rather than
        # survey.py line numbers, and no account of how survey.py fills them, because nothing
        # re-reads survey.py to notice when a cited line or an internal has moved — the four
        # line numbers that used to be in this function all pointed at the wrong statement, in
        # a plugin whose whole subject is anchors that no longer resolve.
        #
        # Three sources, and this tuple is all three of them. A screen carrying only a `path`
        # therefore contributes nothing: its file stem would be a fourth source, and the path
        # itself must not be one either — a screen at /dashboard cannot be what excuses the
        # `/dashboard` route finding the lens exists to make.
        for key, field in (("labels", "text"), ("screens", "name"), ("commands", "name")):
            for item in user_surface.get(key) or []:
                add(item.get(field) if isinstance(item, dict) else item)
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
# to be reachable by the regex above — 2 to 6 characters — or it is dead weight: the regex never
# produces the token, so the entry excuses nothing and the word is never reported either. Four
# entries were unreachable in exactly that way. They are listed as removed at the end of the set
# so that the next reader does not put them back.
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
 # Removed: "N", "A", "I" — ACRONYM needs two characters, so none of them could ever match and
 # the three entries excused nothing.
 # Removed: "WARNING" — seven characters against ACRONYM's six-character ceiling, so it could
 # never match either. The entry read as a promise to excuse a word the check cannot raise.
 # A-034 keeps the ceiling and drops the entry rather than widening the regex to reach it.
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
# Two or more capitalised words ending at the match: the shape of a proper noun.
PROPER_NOUN = re.compile(r"\b[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)+$")
CAPS_RUN = re.compile(r"\b[A-Z][A-Z0-9]{1,}\b(?:\s*(?:&|and|/)?\s*\b[A-Z][A-Z0-9]{1,}\b)+")


# ISO/IEC/IEEE 26514 quality characteristics. Five are marked measurable here and a sixth
# measurable elsewhere, and the marking is not the same claim as being checked: accessibility
# (check_universal), understandability (reading_grade) and subject-fit (check_lens) each have a
# rule below, conciseness is measured only as sentence length feeding that grade, and
# consistency has no check in this file at all. This line said "the four marked measurable are
# checked below", which named a set of the wrong size and promised a check for a term that has
# none.
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


def survey_env_vars(path):
    """Which SCREAMING_SNAKE names are environment variables, according to the CODE.

    `CREDENTIAL_ERROR` is a status the interface displays and `PIONEER_API_URL` is a variable
    somebody exports, and the two are indistinguishable by shape. The question is real; the
    previous answer was not. It read the documentation to decide how to judge the documentation,
    which made the verdict on one page depend on the contents of another: a user page naming
    `PIONEER_API_URL` was clean on its own and a defect once an unrelated install page happened
    to show the same name being assigned. Delete that page and the leak disappeared.

    survey.py already reports every variable the code actually reads, under surface.config, with
    a file:line for each. That is an oracle outside the artifact, which is the only kind worth
    having: nothing a page says can change the answer."""
    if not path:
        return set(), False
    try:
        with open(path, encoding="utf-8") as fh:
            config = (json.load(fh).get("surface") or {}).get("config") or []
        return {c["name"] for c in config
                if isinstance(c, dict) and isinstance(c.get("name"), str)}, True
    except Exception:
        return set(), False


SURVEY_ENV, SURVEY_ENV_LOADED = survey_env_vars(SURVEY_PATH)


def assigned_env_vars(docs):
    """Fallback for when no survey is available: names the docs themselves show being set.

    This is the circular oracle survey_env_vars replaces, kept only so a run with no survey
    still reports something rather than nothing. `check` says which one it used, because a
    verdict reached from the artifact under test is worth less than one reached from the code
    and the reader of the report is entitled to know which they got."""
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
    """Every term the documentation set DEFINES, which is only what a glossary page defines.

    This also collected any acronym appearing in parentheses on any page, on the theory that
    expanding a term defines it. It does, but only for the reader of that page and only from
    that point on, and collecting them here made the set-wide list bypass the per-page ordering
    test entirely: one page closing with "a network security group (NSG)" excused NSG's use in
    the opening line of every other page in the set. A parenthetical is a page-level
    introduction and belongs to expanded_on_page, which tests whether it came in time."""
    terms = set()
    for dirpath, dirnames, filenames in os.walk(docs):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            text = open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace").read()
            if frontmatter(text).get("doc_type") != "glossary":
                continue
            for n, line in prose_lines(text):
                m = re.match(r"^#{2,6}\s+(.+?)\s*$", line)
                if m:
                    terms.add(m.group(1).strip("`*").upper())
                    for a in ACRONYM.findall(m.group(1)):
                        terms.add(a)
                for a in re.findall(r"\(([A-Z][A-Z0-9]{1,5})s?\)", line):
                    terms.add(a)
    return terms


def expanded_on_page(acr, body, before=None):
    """The page introduces the acronym at or before `before`, the offset of its first bare use.

    Position is the whole rule. "A concept is introduced before it is used" is what `pedagogy`
    asks for and what this is named after, and testing only that an expansion exists somewhere
    on the page satisfied a page that used NSG in its opening sentence and expanded it in an
    unrelated closing note. The reader had already met the term and been failed by then.

    `before=None` keeps the old whole-page test, for callers that want existence rather than
    order."""
    limit = len(body) if before is None else before
    for pat in (rf"(?:[A-Za-z][\w-]*\s+){{1,6}}\({acr}s?\)",
                rf"\b{acr}s?\b\s*\([^)]{{4,90}}\)"):
        m = re.search(pat, body)
        if m and m.start() <= limit:
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

    # A reference page's entries ARE the names it lists, and the lens read the audience without
    # ever reading the type. A page whose job is to name every metric reported one finding per
    # metric: on one real docs set, 29 findings on one page and 18 on another were 47 of its 56
    # leaks, all of them the same finding. Architecture words stay forbidden here, because a
    # page listing metrics still has no business saying "the handler".
    entries_are_the_subject = dt in ("reference", "api-reference")

    named = []
    for n, line in prose_lines(text):
        if not entries_are_the_subject:
            for m in CODE_IDENT.finditer(line):
                if m.group(1).lower() not in IDENT_ALLOW:
                    named.append((n, f"`{m.group(1)}`", "an internal symbol name"))
            for m in ENV_VAR.finditer(line):
                if audience == "user" and m.group(1) in env_vars:
                    named.append((n, f"`{m.group(1)}`", "an environment variable"))
        # LENS_MAY_NOT['user'] names "any route path" and "command-line flags" among the
        # things a user page may not name. LENS_MAY_NOT['operator'] names only internal
        # symbols and architecture, so an operator page may name a route or a flag because
        # that line leaves them out — left out for the reader AUDIENCES['operator'] describes,
        # comfortable with a terminal and assuming one, not because LENS_MAY_NOT says anything
        # about terminals itself. Running these two for an operator reported a page for naming
        # `/api/health` when that page is allowed to be about exactly that, and widening
        # ROUTE_PATH would have multiplied the false finding. CODE_IDENT and ARCH_HARD stay
        # outside this gate because both lines forbid them (FR-017, GI-003).
        if audience == "user" and not entries_are_the_subject:
            for m in ROUTE_PATH.finditer(line):
                named.append((n, m.group(1) or m.group(2), "a request route"))
            for m in FLAG.finditer(line):
                named.append((n, f"`{m.group(1)}`", "a command-line flag"))
        for m in ARCH_HARD.finditer(line):
            # "AWS Cloud Controller Manager" is the name of a thing, not a word for the
            # machinery: an operator meets it in Kubernetes' own release notes. A capitalised
            # word inside a run of capitalised words is part of a name, and the rule was
            # reporting two provider pages for naming the component they install.
            if m.group(1)[:1].isupper() and PROPER_NOUN.search(line, 0, m.end()):
                continue
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
    # they actually face. One that explains a mechanism they cannot touch is a developer page
    # wearing the wrong frontmatter.
    #
    # This counted second-person pronouns, which measured tone rather than subject. A page made
    # entirely of "You should know the status field moves between states. You can see that the
    # handler writes it." passed: pure mechanism, addressed warmly. What the rule is named after
    # is what the page is ABOUT, so what it counts now is vocabulary. Machinery words against
    # product words: the architecture terms the lens already knows, against the product's own
    # screens, labels and commands from the survey. A page whose machinery outnumbers its
    # product is explaining the machine.
    if audience == "user" and dt == "explanation":
        body = "\n".join(l for _, l in prose_lines(text, tables=False))
        words = len(re.findall(r"[A-Za-z][A-Za-z']+", body))
        machine = len(ARCH_HARD.findall(body)) + len(ARCH_SOFT.findall(body))
        product = sum(1 for t in SURVEY_ALLOW if t and len(t) > 3
                      and re.search(rf"\b{re.escape(t)}\b", body, re.I))
        product += len(SECOND_PERSON.findall(body)) // 8
        if words >= 200 and machine >= 3 and machine > product:
            defects.append({"page": rel, "rule": "explains-mechanism",
                            "problem": f"a 'user' explanation page naming the machinery "
                                       f"{machine} times against {product} mentions of the "
                                       f"product, in {words} words",
                            "fix": "an explanation for a user exists to settle a choice they "
                                   "face. If it explains something they cannot act on, it is a "
                                   "developer page"})
    return defects, advisories


def check_jargon(rel, text, audience, known):
    """A term used before the reader has met it. This is the mechanical half of the Readable
    gate, which otherwise depends on a reviewer being available.

    `known` is the glossary, and clearing a term against it is a statement of trust rather than
    a verification: nothing here reads a definition to see whether it is correct, and a glossary
    entry saying NSG is a Dutch pastry clears the term as completely as the right one does. The
    terms cleared that way are counted and reported, so a set that passes on the glossary's word
    says so out loud instead of passing quietly."""
    findings, on_trust = [], set()
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
            if a in UNIVERSAL_ACRONYMS or a in seen:
                continue
            if a in known:
                on_trust.add(a)
                continue
            if ON_SCREEN_CUE.search(line[:m.start()]):
                continue
            if any(x <= m.start() < y for x, y in runs):
                continue
            first = re.search(rf"\b{a}s?\b", body)
            if expanded_on_page(a, body, first.start() if first else None):
                continue
            seen.add(a)
            # The trailing `s?` is outside the capture, so a page writing K3s reported the
            # term as K3 and told the writer to expand "(K3)", which is not a word anybody
            # would write. Report what is on the page.
            word = m.group(0)
            findings.append({"page": f"{rel}:{n}", "rule": "undefined-jargon",
                             "problem": f"'{word}' is used without ever being expanded",
                             "fix": f"write it out the first time, '... ({word})', or add "
                                    f"it to the glossary"})
    if linked_glossary:
        for f in findings:
            f["fix"] += "; the page links the glossary but this term is not in it"
    trust = []
    if on_trust:
        trust.append({"page": rel, "rule": "glossary-trusted",
                      "problem": f"{len(on_trust)} term(s) cleared only by a glossary entry "
                                 f"nobody checked: " + ", ".join(sorted(on_trust)[:8]),
                      "fix": "the glossary says what these mean and no check reads a definition "
                             "for correctness. Confirm them before this ships unread"})
    if audience == "user":
        return findings, trust
    return [], findings + trust


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
        return ("no survey: the lens has no allowlist and environment variables are judged "
                "from what the docs themselves say, which is the artifact judging itself. "
                "Pass WEBSTER_SURVEY=<survey.json>")
    if not SURVEY_LOADED:
        return f"no survey: WEBSTER_SURVEY {SURVEY_PATH} could not be read"
    return (f"survey: {len(SURVEY_ALLOW)} product terms and {len(SURVEY_ENV)} environment "
            f"variables, from {SURVEY_PATH}")


def run_check(docs, override):
    defects, advisories, untyped, typed, stubs, pages = [], [], [], 0, 0, 0
    known = glossary_terms(docs)
    env_vars = SURVEY_ENV if SURVEY_ENV_LOADED else assigned_env_vars(docs)
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
    # Zero pages read as writing is not the same as every page passing. A tree of skeletons
    # resolved every rule it had and printed the pass line below, and so did a docs directory
    # holding no page at all, because this gate asked for stubs > 0 rather than for pages == 0
    # — the stub count was never the question. `untyped` and `advisories` can only be filled
    # on the same path that increments `pages`, so defects are the only other thing left to
    # ask about, and FR-040 answers it in its own words: "Frontmatter defects on stubs are real
    # findings; exit 2 'nothing to check' only when there are zero non-stub pages AND zero
    # defects." A run that did find something must never report that there was nothing to look
    # at (FR-040, CT-004).
    if not pages and not defects:
        print(f"{stubs} stubs, nothing to check")
        return 2
    # "every page" spoke for a population that included the stubs. A stub declares a doc_type of
    # its own and is never matched against it: `stubs += 1` and its `continue` sit above
    # `pages += 1` in run_check, so check_typed never sees one, and the header line above counts
    # it in a field of its own rather than among the pages. A tree of one clean how-to and
    # one stub whose placeholder braces name `create_item`, `/dashboard` and `--verbose` printed
    # the sentence and exited 0, while that same stub with nothing changed but the marker taken
    # out reported those three as wrong-lens defects. This branch is only reached when `untyped`
    # is empty and no page carried an unknown doc_type, so every page counted in `pages` did
    # reach check_typed and the narrowed sentence is exactly true of them. The stub count rides
    # beside it so the reader can see what the sentence is not about (AC-020, AC-021, FR-014).
    if not defects and not advisories and not untyped:
        matched = "every written page matches its declared type"
        if stubs:
            matched += f"; {stubs} stubs were not matched against theirs"
        print(matched)
    return 1 if defects else 0


if __name__ == "__main__":
    sys.exit(main())
