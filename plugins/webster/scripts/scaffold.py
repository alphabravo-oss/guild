#!/usr/bin/env python3
"""Create and validate the documentation tree, in the layout Harvester uses.

  init   write the tree, the _category_.json files, and optionally the Docusaurus site
  check  validate an existing tree against the layout, exit 1 on any violation

The layout is subject-first: a directory per thing in the product, each with an overview page
named after the subject and task pages named as verbs. Diataxis lives inside a subject, not at
the top level. Reference is extracted and kept apart.
"""
import argparse, json, os, re, sys

# Fixed sections and their sidebar positions. Subjects are numbered between install and advanced.
FRONT = [("getting-started", "Getting Started", 2), ("install", "Installation", 3)]
BACK = [("advanced", "Advanced", 91), ("troubleshooting", "Troubleshooting", 92),
        ("developer", "Developer", 93)]
# api/ is generated from an OpenAPI spec by docusaurus-plugin-openapi-docs, the way Harvester
# builds docs/api from its swagger files. Scaffolded when asked for, never required.
OPTIONAL = [("api", "API Reference", 90)]
ROOT_PAGES = ["index.md", "faq.md"]
# Pages every getting-started carries. Harvester ships both; they are cheap and they pay off.
GETTING_STARTED = [("overview.md", "quickstart"), ("glossary.md", "glossary"),
                   ("document-conventions.md", "explanation")]

# the content type each fixed section holds
SECTION_TYPE = {"install": "how-to", "advanced": "explanation",
                "troubleshooting": "troubleshooting", "developer": "explanation",
                "api": "api-reference"}

# who each fixed section is written for. The tree serves more than one reader, and pretending
# otherwise is what produced install and developer pages pitched at someone with no terminal.
SECTION_AUDIENCE = {"getting-started": "user", "install": "operator", "advanced": "operator",
                    "troubleshooting": "user", "developer": "developer", "api": "developer"}

SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def category(path, label, position):
    with open(os.path.join(path, "_category_.json"), "w") as f:
        json.dump({"position": position, "label": label,
                   "collapsible": True, "collapsed": True}, f, indent=4)
        f.write("\n")


def skeleton_for(doc_type):
    """The starting shape for a content type, from doctype.py, which carries the Good Docs
    sections. A skeleton is a starting point and never a validation rule."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from doctype import SKELETON
        return SKELETON.get(doc_type, "")
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
    body = skeleton_for(doc_type) if doc_type else ""
    with open(path, "w") as f:
        f.write("\n".join(fm) + ("\n" + body if body else ""))
    return True


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
                sys.exit(f"subject key '{key}' must be lower-case-with-hyphens")
            out.append((key, label.strip() or key.replace("-", " ").capitalize()))
    return out


def do_init(a):
    docs = a.docs
    os.makedirs(docs, exist_ok=True)
    made = []

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

    subjects = parse_subjects(a.subject or [])
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

    print(json.dumps({"created": made, "subjects": [k for k, _ in subjects],
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


def do_check(a):
    docs, bad = a.docs, []
    if not os.path.isdir(docs):
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
            except Exception:
                bad.append({"where": f, "problem": "unreadable _category_.json"})
    for pos, dirs in positions.items():
        if len(dirs) > 1:
            bad.append({"where": ", ".join(dirs), "problem": f"share sidebar position {pos}"})

    # every page carries frontmatter, and its slug is lower-case-with-hyphens
    for dirpath, dirnames, filenames in os.walk(docs):
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
