#!/usr/bin/env python3
"""Create and validate the documentation tree, in the layout Harvester uses.

  init   write the tree, the _category_.json files, and optionally the Docusaurus site.
         status ok at exit 0 once the tree is written; exit 2 with a JSON status when it could
         not run -- bad_subject for a --subject key that cannot become a directory name or
         a label that cannot be written, cannot_write for a directory or a page the filesystem
         refused and for a --title or a --description carrying a byte no page can hold. init
         never exits 1.
  check  validate an existing tree against the layout. status violations at exit 1 on any
         violation, status ok at exit 0 when there are none, and exit 2 with a JSON status
         when the check could not run to completion -- no_docs when nothing is at --docs or
         what is there is not a directory, cannot_read when the filesystem refused a read:
         --docs itself, a page that could not be opened, or a directory at any depth that
         could not be listed. cannot_read carries no violations list, and it can arrive with
         part of the tree already read; the violations collected before the refusal are
         dropped rather than reported, because a findings list from a scan that stopped
         reads as the findings and is not them.

Every one of those is a JSON object on stdout whose first key is `status`, init's exit-0
envelope included. It carries one for the same reason the others do: a caller reading `status`
to tell bad_subject from cannot_write would otherwise get a KeyError on the single path where
nothing went wrong, and a field you have to guard on the good run is a field nobody trusts on
the bad ones. The 1 and the 2 are not interchangeable, which is why init states an exit set of
its own even though it has no violations to report. Each of the three exit-2 statuses used to
leave through exit 1 instead -- a `sys.exit(str)` for the bad key, an uncaught OSError for the
refused write and for the refused read -- so a caller reading JSON got an empty stdout and a
code telling it to go fix a layout that had never been written or looked at.

Two later ones are closed here the same way. A --title, a --description or a --subject label
carrying a byte that is not valid UTF-8 reaches this script as a lone surrogate, which no
encoder will take back out, so the page write raised where it was asked to: exit 1 again, and
over a part-written tree -- index.md alone for a --title, index.md and faq.md and two whole
sections for a --subject label, measured. A directory below the top level that nothing can
list failed in the opposite direction -- os.walk drops what it cannot scan and carries on --
so the pages inside it were checked as though they were not there and the run ended at exit 0,
the code that says a tree was read and found sound.

The layout is subject-first: a directory per thing in the product, each with an overview page
named after the subject and task pages named as verbs. Diataxis lives inside a subject, not at
the top level. Reference is extracted and kept apart.
"""
import argparse, json, os, re, stat, sys

# Fixed sections and their sidebar positions. Subjects are numbered between install and advanced.
FRONT = [("getting-started", "Getting Started", 2), ("install", "Installation", 3)]
BACK = [("advanced", "Advanced", 91), ("troubleshooting", "Troubleshooting", 92),
        ("developer", "Developer", 93)]
# api/ is generated from an OpenAPI spec by docusaurus-plugin-openapi-docs, the way Harvester
# builds docs/api from its swagger files. Scaffolded when asked for, never required.
OPTIONAL = [("api", "API Reference", 90)]
ROOT_PAGES = ["index.md", "faq.md"]
# Pages every getting-started carries. Harvester ships both; they are cheap and they pay off.
# No page about how the documentation itself is written. A reader came to use the product, and
# a product that explains its own notation before explaining itself has got the order wrong.
GETTING_STARTED = [("overview.md", "quickstart"), ("glossary.md", "glossary")]

# the content type each fixed section holds
SECTION_TYPE = {"install": "how-to", "advanced": "explanation",
                "troubleshooting": "troubleshooting", "developer": "explanation",
                "api": "api-reference"}

# who each fixed section is written for. The tree serves more than one reader, and pretending
# otherwise is what produced install and developer pages pitched at someone with no terminal.
SECTION_AUDIENCE = {"getting-started": "user", "install": "operator", "advanced": "operator",
                    "troubleshooting": "user", "developer": "developer", "api": "developer"}

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

# Every page this script writes is UTF-8, said rather than inherited. open()'s default
# encoding is whatever the locale resolves to, so the same --title writes cleanly under one
# LANG and raises under another, and unencodable() below could not then be exact about what
# a write will take. The _category_.json files and the site config are written without it
# because json.dumps escapes everything above ASCII, which every encoding here can hold.
PAGE_ENCODING = "utf-8"


def unencodable(text):
    """Why `text` cannot go into a page, or None when it can.

    argv is decoded before this script sees it, and a byte that is not valid UTF-8 survives
    that decode as a lone surrogate (PEP 383) no encoder will take back out. Reaching the
    page write, it raised UnicodeEncodeError there -- a ValueError, not the OSError the
    boundary in do_init catches -- so init left through a traceback: exit 1, the code check
    reserves for a layout violation, with half a tree written under it. Asked here instead,
    above the first write, because an argument that cannot be written is a typo, and the
    whole point of validating a typo early is that nothing is on disk to clean up.
    """
    try:
        text.encode(PAGE_ENCODING)
    except UnicodeEncodeError as e:
        return f"{e.reason} at position {e.start}"
    return None


def category(path, label, position):
    with open(os.path.join(path, "_category_.json"), "w") as f:
        json.dump({"position": position, "label": label,
                   "collapsible": True, "collapsed": True}, f, indent=4)
        f.write("\n")


def skeleton_for(doc_type, audience=None):
    """The starting shape for a content type, from doctype.py, which carries the Good Docs
    sections. A skeleton is a starting point and never a validation rule."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from doctype import SKELETON
        # Some types need a different starting shape per reader. An explanation page for someone
        # who uses the product is a decision, not a mechanism, so it starts from a decision.
        return SKELETON.get(f"{doc_type}@{audience}") or SKELETON.get(doc_type, "")
    except Exception:
        return ""


def stub(path, title, position, label=None, slug=None, description="",
         doc_type=None, audience=None):
    if os.path.exists(path):
        return False
    fm = ["---", f"sidebar_position: {position}"]
    if label:
        fm.append(f"sidebar_label: {label}")
    if slug:
        fm.append(f"slug: {slug}")
    fm += [f'title: "{title}"']
    if doc_type:
        fm.append(f"doc_type: {doc_type}")
    if audience:
        fm.append(f"audience: {audience}")
    if description:
        fm.append(f"description: {description}")
    fm += ["---", "", f"# {title}", "", "<!-- webster: not written yet -->", ""]
    body = skeleton_for(doc_type, audience) if doc_type else ""
    with open(path, "w", encoding=PAGE_ENCODING) as f:
        f.write("\n".join(fm) + ("\n" + body if body else ""))
    return True


class BadSubject(Exception):
    """A subject key that cannot become a directory name.

    Raised, not exited: the caller chooses the envelope and the exit code. This used to be a
    `sys.exit(str)` here in parse_subjects, which sent the message to stderr and exited 1 —
    the same code do_check returns for a real layout violation — leaving a caller that reads
    JSON with an empty stdout and no way to tell a typo from a broken tree.
    """

    def __init__(self, key, error):
        super().__init__(error)
        self.key = key
        self.error = error


def parse_subjects(raw):
    """key:Label pairs. The key becomes the directory, the label the sidebar entry."""
    out = []
    for item in raw:
        for part in item.split(","):
            part = part.strip()
            if not part:
                continue
            key, _, label = part.partition(":")
            key = key.strip()
            if not SLUG.match(key):
                raise BadSubject(key, f"subject key '{key}' must be lower-case-with-hyphens")
            label = label.strip() or key.replace("-", " ").capitalize()
            # The label is written into the subject's landing page, so a label no page can
            # hold is as unusable as a key no directory can be named after. Both are found
            # here, in the one place a --subject item is judged, and before anything exists.
            why = unencodable(label)
            if why:
                raise BadSubject(key, f"subject label for '{key}' carries a byte no page can "
                                      f"hold: {why}")
            out.append((key, label))
    return out


def do_init(a):
    docs = a.docs
    # Parse every subject before the first write. This call used to sit below index.md, faq.md
    # and the getting-started and install sections, so one mistyped key left a half-built tree
    # on disk. Nothing is created until every key is known to be usable.
    try:
        subjects = parse_subjects(a.subject or [])
    except BadSubject as bad:
        print(json.dumps({"status": "bad_subject", "subject": bad.key, "error": bad.error}))
        return 2

    # The other two arguments that become page text, asked the same question before the same
    # first write: --title is index.md's title and its sidebar label, --description its
    # description line. Those two and the subject labels above are the whole of it: every
    # other argument reaches a file either as a path, which the filesystem encoding hands
    # back byte for byte, or through json.dumps, which escapes a lone surrogate to an ASCII
    # \udcff -- so the site config and the _category_.json labels cannot raise. Reported
    # as cannot_write because that is what it is, a page whose text cannot be written, and
    # the reader acts on it the way they act on a refused one. `created` is empty and means
    # it: the answer is known before the first makedirs.
    for option, text in (("--title", a.title), ("--description", a.description)):
        why = unencodable(text)
        if why:
            print(json.dumps({"status": "cannot_write",
                              "path": os.path.join(docs, "index.md"),
                              "error": f"{option} carries a byte no page can hold: {why}",
                              "created": []}))
            return 2

    # Everything from here writes, and every write can be refused. os.makedirs raises
    # FileExistsError when --docs names a regular file, NotADirectoryError for a path under
    # one, PermissionError for a parent nobody may write; category(), stub() and write_site()
    # raise the same family further in. Unguarded, each of them left main() through a
    # traceback, and a traceback exits 1 -- the code CT-005 reserves for a layout violation --
    # with nothing on stdout, so a caller reading JSON got an empty string where an envelope
    # belongs and a code claiming the tree is wrong when the truth is that nothing was written
    # at all. One boundary rather than a guard per write: four handlers of the same shape
    # disagree the first time one of them is edited. slop.py's main() answers the read side of
    # this class the same way, with a cannot-read line at exit 2.
    made = []
    try:
        os.makedirs(docs, exist_ok=True)

        if stub(os.path.join(docs, "index.md"), a.title, 1,
                label=f"{a.title} Overview", slug="/", description=a.description,
                doc_type="explanation", audience="user"):
            made.append("index.md")
        if stub(os.path.join(docs, "faq.md"), "FAQ", 99, doc_type="explanation",
                audience="user"):
            made.append("faq.md")

        for name, label, pos in FRONT:
            d = os.path.join(docs, name)
            os.makedirs(d, exist_ok=True)
            category(d, label, pos)
            if name == "getting-started":
                for i, (page, dtype) in enumerate(GETTING_STARTED, 1):
                    t = page[:-3].replace("-", " ").capitalize()
                    if stub(os.path.join(d, page), t, i, doc_type=dtype, audience="user"):
                        made.append(f"{name}/{page}")
            elif stub(os.path.join(d, f"{name}.md"), label, 1,
                      doc_type=SECTION_TYPE.get(name, "how-to"),
                      audience=SECTION_AUDIENCE.get(name, "user")):
                made.append(f"{name}/{name}.md")

        for i, (key, label) in enumerate(subjects, start=4):
            d = os.path.join(docs, key)
            os.makedirs(d, exist_ok=True)
            category(d, label, i)
            # the landing page. Its name is free; sidebar_position 1 is what makes it the landing page.
            if stub(os.path.join(d, f"{key}.md"), label, 1, doc_type="explanation",
                    audience=a.subject_audience):
                made.append(f"{key}/{key}.md")

        sections = list(BACK) + (list(OPTIONAL) if a.api else [])
        for name, label, pos in sections:
            d = os.path.join(docs, name)
            os.makedirs(d, exist_ok=True)
            category(d, label, pos)
            if stub(os.path.join(d, f"{name}.md"), label, 1,
                    doc_type=SECTION_TYPE.get(name, "explanation"),
                    audience=SECTION_AUDIENCE.get(name, "user")):
                made.append(f"{name}/{name}.md")

        if a.site:
            made += write_site(a)
    except OSError as e:
        # `created` is a floor, not a census: the first makedirs failing does leave the tree
        # untouched, but a refusal further in does not, and an envelope implying an empty disk
        # while half a tree sits on it is the same false report as the exit code above. It
        # lists what do_init had recorded when the write was refused. e.filename is the path
        # the OS actually refused, which is not always `docs`.
        print(json.dumps({"status": "cannot_write", "path": e.filename or docs,
                          "error": e.strerror or type(e).__name__, "created": made}))
        return 2

    # `status` first, like every other envelope this script prints. The success path was the one
    # exception, and the docstring above claimed otherwise, so a caller that read `status` to
    # tell bad_subject from cannot_write hit a KeyError on the ordinary run -- which teaches it
    # to stop reading the field at all and go back to guessing from the exit code.
    print(json.dumps({"status": "ok", "created": made,
                      "subjects": [k for k, _ in subjects],
                      "docs": docs, "site": a.site_dir if a.site else None}, indent=2))
    return 0


def write_site(a):
    """A Docusaurus site in the same shape as Harvester's: classic preset, autogenerated
    sidebar from the filesystem, versioning ready, edit links on.

    It goes in its own directory. Writing these files to a repo root collides with whatever
    application already lives there, and on a Next.js repo src/css lands inside the app source.
    """
    made = []
    site = a.site_dir
    os.makedirs(site, exist_ok=True)
    # the docs path is resolved from inside the site directory
    docs_from_site = os.path.relpath(os.path.abspath(a.docs), os.path.abspath(site))
    cfg = f"""// @ts-check
const {{themes}} = require('prism-react-renderer');

/** @type {{import('@docusaurus/types').DocusaurusConfig}} */
const config = {{
  title: {json.dumps(a.title)},
  tagline: {json.dumps(a.description)},
  url: {json.dumps(a.url)},
  baseUrl: '/',
  onBrokenLinks: 'warn',
  favicon: 'img/favicon.ico',
  organizationName: {json.dumps(a.org)},
  projectName: {json.dumps(a.project)},
  i18n: {{ defaultLocale: 'en', locales: ['en'] }},
  presets: [
    ['classic', ({{
      docs: {{
        path: {json.dumps(docs_from_site)},
        routeBasePath: '/',
        sidebarPath: require.resolve('./sidebars.js'),
        showLastUpdateTime: true,
        editUrl: {json.dumps(a.edit_url)},
      }},
      blog: false,
      theme: {{ customCss: require.resolve('./src/css/custom.css') }},
    }})],
  ],
  themeConfig: {{
    navbar: {{
      title: {json.dumps(a.title)},
      items: [{{ type: 'docsVersionDropdown', position: 'right' }}],
    }},
    prism: {{ theme: themes.github, darkTheme: themes.dracula }},
  }},
}};

module.exports = config;
"""
    sidebars = """// @ts-check
/** The sidebar is generated from the filesystem, so the directory layout is the navigation.
 *  Order comes from each directory's _category_.json position and each page's sidebar_position. */
/** @type {import('@docusaurus/plugin-content-docs').SidebarsConfig} */
module.exports = {
  docs: [{ type: 'autogenerated', dirName: '.' }],
};
"""
    pkg = {
        "name": f"{a.project}-docs", "version": "0.0.0", "private": True,
        "scripts": {"start": "docusaurus start", "build": "docusaurus build",
                    "serve": "docusaurus serve",
                    "version": "docusaurus docs:version"},
        "dependencies": {"@docusaurus/core": "^3.9.2",
                         "@docusaurus/preset-classic": "^3.9.2",
                         "prism-react-renderer": "^2.4.1",
                         "react": "^18.3.1", "react-dom": "^18.3.1"},
        "engines": {"node": ">=20.0"},
    }
    for name, body in [("docusaurus.config.js", cfg), ("sidebars.js", sidebars),
                       ("package.json", json.dumps(pkg, indent=2) + "\n"),
                       (".gitignore", "node_modules/\nbuild/\n.docusaurus/\n")]:
        path = os.path.join(site, name)
        if not os.path.exists(path):
            open(path, "w").write(body)
            made.append(path)
    css = os.path.join(site, "src", "css")
    os.makedirs(css, exist_ok=True)
    if not os.path.exists(os.path.join(css, "custom.css")):
        open(os.path.join(css, "custom.css"), "w").write(
            "/* Site styling. Pick a palette with a point of view rather than the default. */\n")
        made.append(os.path.join(css, "custom.css"))
    return made


def unreadable(error):
    """os.walk's onerror. Re-raises instead of walking on.

    Left at its default, os.walk swallows the error from a directory it cannot scan and
    carries on with the rest of the tree, so every page inside that directory goes unread
    and the run still ends in the ok envelope at exit 0 -- a tree reported sound on the
    strength of a scan that skipped part of it, which is a worse answer than any violation.
    Raising hands the error to do_check's boundary, which has an exit code for a tree it
    could not read. slop.py's files() answers the same class the same way.
    """
    raise error


def do_check(a):
    docs, bad = a.docs, []

    # Every read below can be refused, the one that asks whether --docs is a directory
    # included. A *.md that is a dangling symlink is listed like any
    # other page and fails at open(); a directory nobody may list raises on os.listdir. Left
    # alone each of those left do_check through a traceback, and a traceback exits 1 -- the
    # code this script reserves for a real layout violation -- with nothing on stdout, so a
    # caller reading JSON got no envelope at all and a code telling it to go fix a tree that
    # had never been read. One boundary rather than a guard per read, for the reason do_init
    # gives above; slop.py answers the same class with a cannot-read line at exit 2. The
    # walk below carries an onerror for the same reason at one more remove: os.listdir raises
    # here for docs/ and for each directory directly under it, but a subdirectory below one of
    # those is reached by os.walk alone, which drops what it cannot scan without saying so.
    try:
        # os.path.isdir used to open this function, and it answers a wider question than the
        # one it looks like. It calls os.stat and returns False for every error the call
        # raises, so "nothing is at --docs", "what is there is not a directory" and "the
        # filesystem refused to say" all left through the same no_docs envelope -- a
        # directory that is there, holding pages, reported as absent, while init on the
        # identical path in the identical state reported cannot_write. One script, two
        # answers to one question, and the wrong one is the one that reads as a clean tree
        # that simply has not been written yet. The two absences are answered here; every
        # refusal falls through to the boundary below, which is where this file already
        # publishes a read it was refused.
        try:
            present = stat.S_ISDIR(os.stat(docs).st_mode)
        except (FileNotFoundError, NotADirectoryError):
            present = False
        if not present:
            print(json.dumps({"status": "no_docs", "docs": docs})); return 2

        for page in ROOT_PAGES:
            if not os.path.isfile(os.path.join(docs, page)):
                bad.append({"where": f"{docs}/{page}", "problem": "required root page is missing"})

        required = [n for n, _, _ in FRONT] + [n for n, _, _ in BACK]
        known = required + [n for n, _, _ in OPTIONAL]
        for name in required:
            if not os.path.isdir(os.path.join(docs, name)):
                bad.append({"where": f"{docs}/{name}/", "problem": "required section is missing"})

        top = sorted(d for d in os.listdir(docs)
                     if os.path.isdir(os.path.join(docs, d)) and not d.startswith("."))
        subjects = [d for d in top if d not in known]

        for d in top:
            p = os.path.join(docs, d)
            if not os.path.isfile(os.path.join(p, "_category_.json")):
                bad.append({"where": f"{docs}/{d}/", "problem": "no _category_.json, so sidebar order is undefined"})
            firsts = [f for f in sorted(os.listdir(p)) if f.endswith(".md")
                      and re.search(r"^sidebar_position:\s*1\s*$",
                                    open(os.path.join(p, f), encoding="utf-8",
                                         errors="replace").read(400), re.M)]
            if not firsts:
                bad.append({"where": f"{docs}/{d}/",
                            "problem": "no landing page; one page in the directory needs sidebar_position: 1"})
            elif len(firsts) > 1:
                bad.append({"where": ", ".join(f"{d}/{f}" for f in firsts),
                            "problem": "more than one page claims sidebar_position: 1"})

        positions = {}
        for d in top:
            f = os.path.join(docs, d, "_category_.json")
            if os.path.isfile(f):
                try:
                    pos = json.load(open(f)).get("position")
                    positions.setdefault(pos, []).append(d)
                # The three families a malformed file raises here, named rather than caught as
                # `Exception`: ValueError for bytes that are not UTF-8 or not JSON,
                # AttributeError for a top-level array, which has no .get, and TypeError for a
                # position that is itself a list, which setdefault cannot key on. All three are
                # a file Docusaurus will not order a sidebar from, which is a violation. What
                # `Exception` also swallowed was OSError -- a file that could not be read at all
                # -- and reporting that as a violation is the same wrong answer the boundary
                # above exists to stop, one file further in.
                except (ValueError, AttributeError, TypeError):
                    bad.append({"where": f, "problem": "unreadable _category_.json"})
        for pos, dirs in positions.items():
            if len(dirs) > 1:
                bad.append({"where": ", ".join(dirs), "problem": f"share sidebar position {pos}"})

        # every page carries frontmatter, and its slug is lower-case-with-hyphens
        for dirpath, dirnames, filenames in os.walk(docs, onerror=unreadable):
            dirnames[:] = [x for x in dirnames if not x.startswith(".")]
            for fn in filenames:
                if not fn.endswith(".md"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, fn), docs)
                if not SLUG.match(fn[:-3]):
                    bad.append({"where": rel, "problem": "filename is not lower-case-with-hyphens"})
                head = open(os.path.join(dirpath, fn), encoding="utf-8", errors="replace").read(400)
                if not head.startswith("---"):
                    bad.append({"where": rel, "problem": "no frontmatter"})
                elif "title:" not in head.split("---")[1]:
                    bad.append({"where": rel, "problem": "frontmatter has no title"})

        # A page sitting in a section whose reader is known should not claim a different one.
        for section, expected in SECTION_AUDIENCE.items():
            d = os.path.join(docs, section)
            if not os.path.isdir(d):
                continue
            for fn in sorted(os.listdir(d)):
                if not fn.endswith(".md"):
                    continue
                head = open(os.path.join(d, fn), encoding="utf-8", errors="replace").read(500)
                m = re.search(r"^audience:\s*(\S+)", head, re.M)
                if m and m.group(1) != expected:
                    bad.append({"where": f"{section}/{fn}",
                                "problem": f"declares audience '{m.group(1)}' but sits in "
                                           f"{section}/, which is written for '{expected}'"})

        # A root page is for a topic that genuinely crosses every subject. Harvester has two beyond
        # index and faq. More than a handful means subjects were never named.
        flat = sorted(f for f in os.listdir(docs)
                      if f.endswith(".md") and f not in ROOT_PAGES)
        if len(flat) > 4:
            bad.append({"where": ", ".join(flat),
                        "problem": f"{len(flat)} cross-cutting root pages; more than 4 means a subject was never named"})

    except OSError as e:
        # No violations here, deliberately, and this is where it differs from cannot_write's
        # `created`: that list names files now sitting on disk that the caller has to deal
        # with, while `bad` from an aborted scan is a floor on a question nobody finished
        # asking. A caller that reads it as the findings is reading a partial answer as a
        # whole one, which is the false pass this exit code exists to refuse.
        print(json.dumps({"status": "cannot_read", "path": e.filename or docs,
                          "error": e.strerror or type(e).__name__}))
        return 2

    print(json.dumps({"status": "ok" if not bad else "violations",
                      "subjects": subjects, "sections": known,
                      "violations": bad}, indent=2))
    return 0 if not bad else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["init", "check"])
    ap.add_argument("--docs", default="docs")
    ap.add_argument("--title", default="Documentation")
    ap.add_argument("--description", default="")
    ap.add_argument("--subject", action="append",
                    help="key:Label, repeatable or comma separated")
    ap.add_argument("--site", action="store_true", help="also write the Docusaurus site")
    ap.add_argument("--site-dir", default="website",
                    help="where the site lives, its own directory so it cannot collide with the app")
    ap.add_argument("--subject-audience", default="user", choices=["user", "operator", "developer"],
                    help="who the subject directories are written for. Most products document "
                         "their own features for the person using them")
    ap.add_argument("--api", action="store_true",
                    help="scaffold docs/api, for a product with an OpenAPI spec to generate from")
    ap.add_argument("--url", default="https://example.com")
    ap.add_argument("--org", default="")
    ap.add_argument("--project", default="docs")
    ap.add_argument("--edit-url", default="")
    a = ap.parse_args()
    return do_init(a) if a.mode == "init" else do_check(a)


if __name__ == "__main__":
    sys.exit(main())
