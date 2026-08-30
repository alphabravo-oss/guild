#!/usr/bin/env python3
"""Detect the stack and enumerate the real public surface of a repo.

Prints JSON. Every entry under `surface` and `user_surface` carries a file:line
anchor, because a surface item without an anchor is a claim and this script is
not allowed to make claims. The arrays whose entries carry no anchor are
`stack`, `frameworks`, `tooling`, `tests.files` and `existing_docs`. All of them
but `tooling` hold bare strings -- a language, a framework name, a path -- and a
string has nowhere to hang an anchor on. `tooling` holds records shaped like the
ones under `surface` and carries none anyway, because a tooling entry recommends
something for this repo rather than reporting something found in it, so there is
no line to send anybody to.

That census has been wrong twice, each time by naming fewer arrays than the
document prints. It opened "Every entry", flatly, which read as covering
everything below it. The narrowing that replaced it said `tooling` was the
single array whose entries carry none, which was false of `stack`, `frameworks`,
`tests.files` and `existing_docs` as well -- and the last two are paths walk()
found inside this repo, so the reason that sentence gave, that a recommendation
has no line to send anybody to, did not even separate them from `tooling`. A
second copy of the claim sat in the comment above CLI_FLAG_DECL both times, and
nothing measured either copy. That is how the flat one came to be narrowed
there a cycle before it was narrowed here; the narrowing itself was corrected in
both places at once, by deleting that copy. So the census is stated here and
nowhere else, and it is not stated by hand:
test_survey.py::test_every_surface_entry_carries_an_anchor reads the names out
of the sentence above and measures them against the arrays a real run prints.

There is no `sys.exit` in this file and no gate to fail: a run that reads the
repo prints the survey and returns 0, whatever it found. What that costs is
paid by the readers below -- a file this script can read but cannot use is
answered with that file's contents missing, never with the survey missing.
"""
import json, os, re, subprocess, sys
# tomllib is standard library from Python 3.11, which is this plugin's floor: webster requires
# 3.11 or newer. A-001 chose no fallback, so an older interpreter fails on the next line with
# ModuleNotFoundError rather than half-running. Nothing here chooses which interpreter runs:
# the commands invoke this file as a bare `python3 scripts/survey.py`, so which one runs is
# whatever PATH resolves, and /usr/bin/python3 on the machine this was written on is 3.9.6.
# The floor is stated because it is a requirement on the caller; the import is what enforces it.
import tomllib

ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
SKIP = {".git", "node_modules", "dist", "build", ".next", "out", "vendor",
        "__pycache__", ".venv", "venv", "target", "coverage", ".turbo"}


def walk(exts=None, names=None):
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP and (not d.startswith(".") or d == ".claude")]
        for fn in filenames:
            if names and fn in names:
                yield os.path.join(dirpath, fn)
            elif exts and os.path.splitext(fn)[1] in exts:
                yield os.path.join(dirpath, fn)


def rel(p):
    return os.path.relpath(p, ROOT)


def read(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def load_json(p):
    """Parse a JSON file into an object, or return {} the way load_toml does for broken TOML.

    A syntax error was already answered this way. A file that parses cleanly and is not an
    object was not: `[1, 2]` is a valid JSON document with no `.get` on it, so a package.json
    holding an array -- or `null`, or a bare string -- killed the whole survey with an
    AttributeError traceback and exit 1, on a file the script could read. The shape is checked
    at the read rather than at every place `pkg` is later reached into, so that a reader added
    after this one cannot forget it."""
    try:
        data = json.loads(read(p))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_toml(p):
    """Parse a TOML file, or return {} the way load_json does for a broken package.json.

    A pyproject.toml is a syntax error for as long as somebody is editing it, and a survey that
    dies there hands the writer no surface at all rather than the rest of a working one."""
    try:
        with open(p, "rb") as f:
            return tomllib.load(f)
    except Exception:
        return {}


def toml_table(data, *keys):
    """The nested table at `keys`, or {}. TOML permits a scalar where a table is expected."""
    for key in keys:
        if not isinstance(data, dict):
            return {}
        data = data.get(key)
    return data if isinstance(data, dict) else {}


def anchors(path, pattern, group=None):
    """Return [{value, anchor}] for each regex match, anchored to file:line."""
    out = []
    for i, line in enumerate(read(path).splitlines(), 1):
        m = re.search(pattern, line)
        if m:
            out.append({"value": m.group(group) if group else m.group(0).strip(),
                        "anchor": f"{rel(path)}:{i}"})
    return out


# ---------------------------------------------------------------- stack
pkg = load_json(os.path.join(ROOT, "package.json"))
# Each dependency table is checked for being a table on the same grounds load_json checks the
# document for being an object: `"dependencies": []` is well-formed JSON that npm would reject
# and `{**[]}` is a TypeError, so the survey died one level below the shape load_json had just
# guaranteed. A field of the wrong type declares no dependencies, which is what an absent field
# declares too, so both are read the same way.
deps = {}
for _field in ("dependencies", "devDependencies"):
    _declared = pkg.get(_field)
    if isinstance(_declared, dict):
        deps.update(_declared)
stack, frameworks = [], []

# A Python project's name and description live in pyproject.toml, and reading only package.json
# meant every one of them was surveyed under the checkout's directory name. PEP 621's [project]
# is read first and Poetry's legacy [tool.poetry] second, per field: Poetry 2.0 writes [project]
# but older files still carry the description under [tool.poetry] alone. package.json still wins
# when both files exist, because a repo with one is a Node project that happens to also declare
# Python tooling.
pyproject = load_toml(os.path.join(ROOT, "pyproject.toml"))
py_meta = {}
for _table in (toml_table(pyproject, "project"), toml_table(pyproject, "tool", "poetry")):
    for _key in ("name", "description"):
        if _key not in py_meta and isinstance(_table.get(_key), str):
            py_meta[_key] = _table[_key]

if pkg:
    stack.append("node")
    if os.path.exists(os.path.join(ROOT, "tsconfig.json")):
        stack.append("typescript")
    for name, label in [("next", "next"), ("react", "react"), ("express", "express"),
                        ("fastify", "fastify"), ("@nestjs/core", "nest"), ("vue", "vue"),
                        ("svelte", "svelte"), ("astro", "astro"), ("hono", "hono"),
                        ("vitest", "vitest"), ("jest", "jest"), ("tailwindcss", "tailwind")]:
        if name in deps:
            frameworks.append(label)

for marker, label in [("pyproject.toml", "python"), ("requirements.txt", "python"),
                      ("go.mod", "go"), ("Cargo.toml", "rust"), ("Gemfile", "ruby")]:
    if os.path.exists(os.path.join(ROOT, marker)) and label not in stack:
        stack.append(label)

py_src = ""
if "python" in stack:
    py_src = " ".join(read(p) for p in list(walk(exts={".py"}))[:200])
    for name, label in [("fastapi", "fastapi"), ("django", "django"), ("flask", "flask")]:
        if re.search(rf"\b{name}\b", py_src, re.I) and label not in frameworks:
            frameworks.append(label)

# ---------------------------------------------------------------- surface
surface = {"http": [], "pages": [], "cli": [], "exports": [], "config": [], "specs": []}
HTTP_VERBS = "GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS"

# Next.js App Router route handlers
for p in walk(names={"route.ts", "route.tsx", "route.js", "route.mjs"}):
    seg = os.path.dirname(rel(p))
    for part in ("src/app", "app"):
        if seg == part:
            seg = ""
            break
        if seg.startswith(part + "/"):
            seg = seg[len(part) + 1:]
            break
    url = "/" + seg if seg else "/"
    for hit in anchors(p, rf"export\s+(?:async\s+)?function\s+({HTTP_VERBS})\b", 1):
        surface["http"].append({"method": hit["value"], "path": url, "anchor": hit["anchor"],
                                "source": "next-app-router"})

# Next.js middleware, which is a real request-path surface and easy to miss
for cand in ("middleware.ts", "middleware.js", "src/middleware.ts", "src/middleware.js"):
    mp = os.path.join(ROOT, cand)
    if os.path.isfile(mp):
        matcher = anchors(mp, r"matcher\s*:\s*\[?\s*['\"]([^'\"]+)", 1)
        surface["http"].append({
            "method": "MIDDLEWARE",
            "path": ", ".join(m["value"] for m in matcher) or "all requests",
            "anchor": f"{cand}:1", "source": "next-middleware"})

# Next.js page routes, so the docs know which screens exist
for p in walk(names={"page.tsx", "page.ts", "page.jsx", "page.js"}):
    seg = os.path.dirname(rel(p))
    for part in ("src/app", "app"):
        if seg == part:
            seg = ""
            break
        if seg.startswith(part + "/"):
            seg = seg[len(part) + 1:]
            break
    surface.setdefault("pages", []).append(
        {"path": "/" + seg if seg else "/", "anchor": f"{rel(p)}:1"})

# Next.js Pages API
for p in walk(exts={".ts", ".js", ".tsx"}):
    r = rel(p)
    if "/api/" in r and (r.startswith("pages/") or r.startswith("src/pages/")):
        url = "/" + re.sub(r"^(src/)?pages/", "", r).rsplit(".", 1)[0]
        surface["http"].append({"method": "ANY", "path": url.replace("/index", "") or "/",
                                "anchor": f"{r}:1", "source": "next-pages-api"})

# Express / Fastify / Hono
for p in walk(exts={".ts", ".js", ".mjs"}):
    for hit in anchors(p, r"\b(?:app|router|server)\.(get|post|put|patch|delete)\s*\(\s*['\"`]([^'\"`]+)"):
        m = re.search(r"\.(get|post|put|patch|delete)\s*\(\s*['\"`]([^'\"`]+)", hit["value"])
        if m:
            surface["http"].append({"method": m.group(1).upper(), "path": m.group(2),
                                    "anchor": hit["anchor"], "source": "node-router"})

# FastAPI / Flask
PY_DECORATOR = re.compile(r"@\w+\.(get|post|put|patch|delete|route)\s*\(\s*['\"]([^'\"]+)")
# Black and ruff move the path onto its own line as soon as a decorator carries two keyword
# arguments and passes 88 columns, which is the ordinary shape of a FastAPI route. Matching one
# line at a time reported those apps as having no HTTP surface at all, and an empty surface
# reads as "nothing to document" rather than as a miss.
PY_DECORATOR_OPEN = re.compile(r"^\s*@\w+\.(?:get|post|put|patch|delete|route)\s*\(\s*$")
# Flask states its verbs in a methods= list and defaults to GET without one. Reporting the
# literal word ROUTE named a verb no client can send, so the docs written from it were wrong
# in the one detail a reader copies.
FLASK_METHODS = re.compile(r"methods\s*=\s*\[([^\]]*)\]")


def call_depth_delta(line, quote=None):
    """How far one line opens or closes a call, over the parentheses that are code.

    Counting them in raw text counts the ones inside string literals and `#` comments too. A
    decorator carrying `strict_slashes=False,  # 2) legacy behaviour, see PROJ-14` balanced its
    call two lines early: the join stopped before `methods=["POST"]`, the text handed to the
    verb decision below had no methods= in it -- which is also what a route declaring none
    looks like -- and GET was published for an endpoint that answers 405 to it. A quote and a
    `#` are the two places a paren is not syntax, so both spans are skipped before anything is
    counted.

    A `#` comment and a single-quoted string end at the newline. A triple-quoted one does not,
    and reading each line as if it did counted the `)` on the string's later lines as code. A
    decorator carrying `doc='''a note` / `with a ) paren inside it'''` alongside
    `methods=["POST"]` balanced the call on that second line, and `GET /exports` was published
    for a route serving POST alone: the same phantom, through the other door. (The example is
    written with the other delimiter because this docstring is itself a triple-quoted string;
    both spellings behave the same and both are tracked.) So the open delimiter leaves one
    line and comes back into the next: `quote` is the delimiter still standing when this line
    ended, and the caller hands it back on the line after. While one is open nothing is
    counted, which is also what leaves a string running past the join's cap holding the depth
    above zero, so the call reads as unfinished rather than as whole."""
    delta, i, n = 0, 0, len(line)
    while i < n:
        if quote:
            j = line.find(quote, i)
            if j < 0:
                return delta, quote
            i, quote = j + 3, None
            continue
        ch = line[i]
        if ch == "#":
            break
        if ch in "\"'":
            if line[i:i + 3] == ch * 3:
                quote = ch * 3
                i += 3
                continue
            i += 1
            while i < n and line[i] != ch:
                i += 2 if line[i] == "\\" else 1
            i += 1
            continue
        if ch == "(":
            delta += 1
        elif ch == ")":
            delta -= 1
        i += 1
    return delta, quote


if "python" in stack:
    for p in walk(exts={".py"}):
        lines = read(p).splitlines()
        for i, line in enumerate(lines, 1):
            joined = line
            depth, quote = call_depth_delta(line)
            if PY_DECORATOR_OPEN.match(line):
                # The cap is a bound on how far the read goes, not a statement about how long
                # a decorator gets. A route carrying six keyword arguments one to a line is
                # ordinary, and the join stops six lines in whether or not the call ended
                # there. What the cap is for is the other direction: a `(` that opened
                # something else must not let the join swallow the function beneath it.
                for cont in lines[i:i + 6]:
                    joined += " " + cont.strip()
                    # The delimiter of a triple-quoted string still open at the end of one
                    # line goes back in at the start of the next, which is the whole of what
                    # makes this a scan of the call rather than of six separate lines.
                    delta, quote = call_depth_delta(cont, quote)
                    depth += delta
                    if depth <= 0:
                        break
            # Whether the call was read to its end, measured over the parentheses that are
            # code and not over the ones inside a string or a comment -- see call_depth_delta
            # for what that distinction cost. Anything below that reasons from something being
            # *absent* has to ask first, because unread and absent are the same text.
            whole_call = depth <= 0
            m = PY_DECORATOR.search(joined)
            if not m:
                continue
            verb, path = m.group(1), m.group(2)
            # unittest.mock spells its decorator `patch`, exactly like the HTTP verb, and the
            # object before the dot is unconstrained, so `@mock.patch("pkg.mod.func")` read as
            # a PATCH endpoint served at `pkg.mod.func`. Joining continuation lines made that
            # worse rather than better: a Black-split mock that used to escape the single-line
            # scan entirely now gets accumulated too. The leading slash is what separates a
            # route from a decorator that only looks like one, and only one of the two
            # frameworks this pass reads enforces it: Flask 3.1.3 raises
            # ValueError("URL rule 'items' must start with a slash."), while FastAPI 0.141.1
            # accepts `@app.get("items")` silently and registers the path exactly as written,
            # asserting nothing. So the slash is this scan's own discriminator rather than a
            # rule both libraries keep, and its price is a slashless FastAPI route going
            # unpublished. That is the direction to fail in: a phantom endpoint is worse than a
            # missing one, because a writer is sent to document it and finds nothing there.
            if not path.startswith("/"):
                continue
            methods = [verb.upper()]
            if verb == "route":
                listed = FLASK_METHODS.search(joined)
                # Flask serves GET for a route that declares no methods, so an absent
                # methods= is itself a declaration -- but only where the whole call was
                # read. Past the cap the two are indistinguishable, and defaulting anyway
                # published GET for a route whose methods=["POST"] sat one line further out:
                # a verb that endpoint answers 405 to, wrong in the one detail a reader
                # copies. The same rule as the leading slash above settles it -- a phantom
                # endpoint is worse than a missing one -- so a route whose verbs were never
                # reached is left out rather than guessed at. ROUTE is not the fallback
                # either; that is the verb no client can send this branch exists to stop.
                if not listed and not whole_call:
                    continue
                methods = ([v.upper() for v in re.findall(r"['\"]([A-Za-z]+)['\"]", listed.group(1))]
                           if listed else []) or ["GET"]
            for method in methods:
                # The anchor is the @ line, not the line the path happened to land on: the
                # decorator is what a reader is sent to read.
                surface["http"].append({"method": method, "path": path,
                                        "anchor": f"{rel(p)}:{i}", "source": "python-decorator"})

# Go handlers
if "go" in stack:
    for p in walk(exts={".go"}):
        for hit in anchors(p, r"Handle(?:Func)?\s*\(\s*\"([^\"]+)"):
            m = re.search(r"\"([^\"]+)", hit["value"])
            if m:
                # Go 1.22 ServeMux patterns are "METHOD /path"; earlier ones are just "/path"
                pat = m.group(1).strip()
                parts = pat.split(None, 1)
                if len(parts) == 2 and parts[0].isupper():
                    method, path = parts
                else:
                    method, path = "ANY", pat
                surface["http"].append({"method": method, "path": path,
                                        "anchor": hit["anchor"], "source": "go-mux"})

# CLI binaries and package exports
for key, bucket in (("bin", "cli"), ("exports", "exports")):
    val = pkg.get(key)
    if isinstance(val, dict):
        for k, v in val.items():
            surface[bucket].append({"name": k, "target": v, "anchor": "package.json:1"})
    elif isinstance(val, str):
        surface[bucket].append({"name": pkg.get("name", "?"), "target": val, "anchor": "package.json:1"})
for key in ("main", "types", "module"):
    if pkg.get(key):
        surface["exports"].append({"name": key, "target": pkg[key], "anchor": "package.json:1"})

# Python entry points, in the same {name, target, anchor} shape as the package.json bin entries
# above: a reader asking which command to type should not have to know which packaging tool
# wrote the file. PEP 621's tables come first and Poetry's legacy one is the fallback, so a
# Poetry 2.0 file that declares both does not report every console script twice. The anchor is
# pyproject.toml:1 because tomllib reports no line numbers.
#
# A target that is not a string names no command to type, and TOML can hold one where the bin
# entries above cannot: json.loads only ever produces values json.dumps can write back, while
# tomllib hands back a datetime.date for TOML's own date syntax, which json.dumps refuses. A
# `[project.scripts]` entry written as a date therefore took the entire survey down at the
# print -- past every extractor, with the whole surface already gathered. Such an entry does
# not claim the name either: a table that declares nothing usable under `foo` must not stop the
# next table in the chain from declaring a real one.
seen_cli = {c["name"] for c in surface["cli"]}
for _table in (toml_table(pyproject, "project", "scripts"),
               toml_table(pyproject, "project", "gui-scripts"),
               toml_table(pyproject, "tool", "poetry", "scripts")):
    for k, v in _table.items():
        if k in seen_cli or not isinstance(v, str):
            continue
        seen_cli.add(k)
        surface["cli"].append({"name": k, "target": v, "anchor": "pyproject.toml:1"})

# Env vars and specs
for envf in (".env.example", ".env.sample", ".env.template"):
    p = os.path.join(ROOT, envf)
    if os.path.exists(p):
        surface["config"] += [{"name": h["value"], "anchor": h["anchor"]}
                              for h in anchors(p, r"^([A-Z][A-Z0-9_]+)=", 1)]
# Env vars read from code. .env.example is what someone remembered to write down;
# this is what the code actually reads, and the difference is usually the finding.
declared = {c["name"] for c in surface["config"]}
# One pattern, named once. It used to be pasted out in full twice — once to find the line and
# once to pick the name back out of it — and two copies of a regex this size drift apart on the
# first edit that only remembers one of them. os.getenv is the reading a Python codebase
# actually uses and was the branch missing from both copies, so a variable read only that way
# was surveyed as declared nowhere and reported as nothing at all. \s* after the bracket
# because a formatter is free to put a space there.
ENV_READ = r"""(?:process\.env\.([A-Z][A-Z0-9_]+)|process\.env\[\s*["']([A-Z][A-Z0-9_]+)|env\[\s*["']([A-Z][A-Z0-9_]+)|os\.environ(?:\.get)?[\[(]\s*["']([A-Z][A-Z0-9_]+)|os\.getenv\(\s*["']([A-Z][A-Z0-9_]+)|os\.Getenv\(\s*["']([A-Z][A-Z0-9_]+))"""
for p in walk(exts={".ts", ".tsx", ".js", ".mjs", ".py", ".go"}):
    for hit in anchors(p, ENV_READ):
        name = next((g for g in re.search(ENV_READ, hit["value"]).groups() if g), None)
        if name and name not in declared:
            declared.add(name)
            surface["config"].append({"name": name, "anchor": hit["anchor"],
                                      "undeclared": True})

for p in walk(exts={".json", ".yaml", ".yml"}):
    base = os.path.basename(p).lower()
    if base.startswith(("openapi", "swagger")):
        surface["specs"].append({"kind": "openapi", "anchor": f"{rel(p)}:1"})

# ---------------------------------------------------------------- user surface
#
# Everything above this line is the surface a developer sees: routes, exports, variables,
# handlers. Documentation written from it describes the machinery, because the machinery is
# what was read. That is the single largest reason a set of documentation reads as though the
# reader already knows the product.
#
# What follows is the surface a person sees: the screens, and the words actually printed on
# them. It is deliberately vocabulary rather than structure, because the words a product puts
# on its own buttons are the words its documentation has to use.

user_surface = {"screens": [], "labels": [], "commands": [], "messages": []}

# Text a component renders. Buttons and headings first, because those are what a reader looks
# for when they are told to go somewhere.
JSX_LABEL = re.compile(
    r"""<(?:[Bb]utton|[Hh][1-4]|[Ll]abel|[Aa]|MenuItem|Tab|NavLink)\b[^>]*>\s*([A-Z][^<>{}\n]{2,48}?)\s*<""")
ATTR_LABEL = re.compile(
    r"""\b(?:label|title|placeholder|aria-label|heading|buttonText|cta)\s*[=:]\s*["']([A-Z][^"']{2,48})["']""")

seen_labels = set()
for p in walk(exts={".tsx", ".jsx", ".vue", ".svelte"}):
    r = rel(p)
    if re.search(r"(^|/)(test|tests|__tests__|stories)/|\.(test|spec|stories)\.", r):
        continue
    for i, line in enumerate(read(p).splitlines(), 1):
        for pat in (JSX_LABEL, ATTR_LABEL):
            for m in pat.finditer(line):
                v = m.group(1).strip()
                if not v or v.upper() == v and len(v) > 12:
                    continue
                if v.lower() in seen_labels:
                    continue
                seen_labels.add(v.lower())
                user_surface["labels"].append({"text": v, "anchor": f"{r}:{i}"})

# What the product says when something goes wrong. A reader meets these before they meet any
# page of documentation, so troubleshooting is written against them rather than around them.
MESSAGE_LITERAL = re.compile(
    r"""(?:Error|error|throw|toast|notify|message)[^"'\n]{0,30}["']([A-Z][^"']{12,110})["']""")
# FastAPI says none of those words. It raises HTTPException, so every 404 and 409 text in a
# FastAPI app was missing from the surface the troubleshooting page is written against, and the
# page ended up paraphrasing errors the reader can read for themselves.
#
# The string is reached through the call's argument list, not through a window of characters
# that happens to start at the call. The difference is the whole of this branch's correctness: a
# free-floating window runs straight past the closing paren, so
# `raise HTTPException(status_code=410)  # "Gone for good now"` published a source comment and
# `HTTPException(404) if missing else fallback("Item was archived")` published the other arm of
# a ternary. Both were handed to a writer as text this product shows a user, and neither is a
# string this product emits at all -- a message the product never says is worse than a message
# missed, because the troubleshooting page is then written against something that cannot happen.
# Staying inside the argument list is therefore the gap's whole job, and the characters it
# refuses are what does it: no `(` and no `)`, so the gap cannot leave the call it began in, and
# no `#`, so it cannot walk into a trailing comment. The newline in the class does no work --
# the scan reads one line at a time and never offers it one -- and is there so the pattern is
# still bounded if it is ever run over joined text. A-018's "within ~40 chars" is the length
# of that argument list before the literal, and the literal must be followed by the `,` or `)`
# that ends an argument.
#
# What keeps a dict or list detail out is not the gap at all: it is the quote required
# immediately after `detail=`, which `detail={"loc": ...}` and `detail=[{...}]` and
# `detail=CODES["x"]` each fail on their first character. The gap used to refuse `{`, `[` and
# quotes as well, on the stated grounds that a collection is a machine-readable payload -- but
# the quote after `detail=` had already settled that, and the only thing those exclusions could
# still reach was a real `detail=` string standing after an earlier argument that happened to be
# a collection or a string: `HTTPException(status_code=422, headers={"X": "1"}, detail="Bad
# input")` published nothing. Dropping a message the product does say is the failure this branch
# exists to fix, so those characters are back in the gap and the comment no longer credits them
# with work the quote was doing. The forty characters are the bound that is left, and it is a
# real one -- an argument list longer than that ahead of the detail is still out of reach, which
# is why the count is A-018's number rather than a rounder one.
#
# Two argument shapes, which are the two A-018 names: `detail=` anywhere in the list, and a
# string sitting second after a status code.
#
# The two branches have different floors and both are counted here the same way: the whole
# captured string, leading capital included. MESSAGE_LITERAL takes thirteen characters and up
# ([A-Z] plus {12,110}); this branch takes four and up ([A-Z] plus {3,110}). A-018 names the
# {3,110} quantifier itself, and four is what it comes to once the capital in front of it is
# counted -- enough for "Not found", which is nine and is the whole of a real 404. This comment
# used to read "four characters ... rather than the twelve above", giving one floor in
# characters and the other in quantifier digits, so the two numbers read as a comparison and
# were not one. tests/test_survey.py measures both rather than restating them.
HTTP_EXCEPTION_MESSAGE = re.compile(
    r"""HTTPException\(\s*"""
    r"""(?:[^\n()#]{0,40}?detail\s*=\s*|[\w.]{1,30}\s*,\s*)"""
    r"""["']([A-Z][^"']{3,110})["']\s*[,)]""")

seen_msgs = set()
for p in walk(exts={".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs"}):
    r = rel(p)
    if re.search(r"(^|/)(test|tests|__tests__)/|\.(test|spec)\.", r):
        continue
    for i, line in enumerate(read(p).splitlines(), 1):
        for pat in (MESSAGE_LITERAL, HTTP_EXCEPTION_MESSAGE):
            for m in pat.finditer(line):
                v = m.group(1).strip()
                if v.lower() in seen_msgs or "{" in v:
                    continue
                seen_msgs.add(v.lower())
                user_surface["messages"].append({"text": v, "anchor": f"{r}:{i}"})

# Subcommands a person types, which is the user surface of anything without a screen.
SUBCOMMAND = re.compile(
    r"""(?:\.command\(|add_parser\(|Use:\s*)["']([a-z][a-z0-9 :_-]{1,40})["']""")
# The other half of what a person types. A subcommand name has to begin with a lowercase letter,
# so a hyphen-led token could never come out of the pattern above, and doctype.py's flag lens --
# which excuses a `--verbose` on a user page when the product's own survey declares it -- had
# nothing to excuse anything with. The suppression path existed end to end and could not fire:
# every flag a command-line product documented was still reported as a leak, and the only way
# out was to name it by hand in WEBSTER_LENS_ALLOW.
#
# Read from the declaration a reader can be sent to: argparse's add_argument, optparse's
# add_option, and the `.option(` that click and commander both spell the same way. yargs
# spells `.option(` too and nothing here reads it, deliberately: its key is the flag's name
# without the dashes -- `.option("out", {alias: "o"})` -- and a token carrying no dash is not
# a spelling this list is able to publish, so the call is read and dropped rather than mined.
#
# What is read from it is the option strings, and those are the call's positional arguments:
# all three declarations take the spellings first, and everything from the first `name=` onwards --
# help=, action=, type=, default=, metavar= -- is metadata about the flag rather than another
# flag. Reading every literal on the line instead meant a keyword's value was mined exactly like
# a spelling, so a help sentence that opened with a flag,
# `add_argument("--keep", help="-x is the short form of --purge")`, declared -x and --purge as
# commands this product has. That is the one direction this branch is not allowed to fail in:
# every name in this list is a term doctype.py stops reporting, so a flag named only in another
# flag's prose arrives downstream as a standing excuse for a real wrong-lens finding on a flag
# the product does not have. The scan therefore stops at the first keyword argument and at the
# end of the call, and a keyword's value is never reached.
#
# A keyword is not the only place a description sits, and stopping at one was not enough.
# commander spells the call `.option(flags, description)`, so the sentence is the
# second *positional* argument: no `name=` ends the scan, the closing paren is the only stop,
# and `.option("--keep", "-x is the short form of --purge")` published --keep, -x and --purge
# together. The same false excuse reached doctype.py by the other door. What ends the positional
# run is therefore the shape of the literal and not the syntax around it -- a flag spec is
# option spellings and nothing else -- and the first literal that is not one is prose, so the
# scan stops on it. One cost, taken deliberately: click accepts the destination name among the
# spellings, and a `.option("upper", "-t", "--to-upper")` written name-first stops on its first
# literal and declares nothing. That direction is the survivable one. A flag this list misses is
# a flag doctype.py reports and an author answers with WEBSTER_LENS_ALLOW; a flag this list
# invents is a finding nobody ever sees.
#
# One option string can still hold several flags, because commander writes "-o, --out <path>" as
# a single literal. Go's flag package and pflag declare a bare name and leave the dashes to the
# framework, so nothing here matches them: a spelling that appears in no file is a claim, and
# every entry under `surface` and `user_surface` carries an anchor exactly so that a reader can
# go and read it. Which of the document's other arrays carry an anchor and which do not is stated
# in the module docstring and measured against a real run from there. It used to be restated here
# as well, and for a cycle the two copies were wrong in two different ways at once: this one had
# been narrowed to name a single anchorless array while the docstring still opened "Every entry",
# because narrowing one copy is not narrowing the other. This copy was deleted rather than
# narrowed a second time. One sentence a test reads is worth more than two kept level by hand.
CLI_FLAG_DECL = re.compile(r"(?:\badd_argument|\badd_option|\.option)\s*\(")
CLI_ARG_END = re.compile(r",\s*\w+\s*=|[)#]")
CLI_STRING = re.compile(r"""["']([^"'\n]*)["']""")
CLI_FLAG_TOKEN = re.compile(r"^--?[A-Za-z][\w-]*$")
# The value a flag takes, which commander writes into the option string beside the spellings:
# `-o, --out <path>` is one literal holding two flags and one placeholder. Angle brackets are
# the required form and square brackets the optional one. Both name an argument rather than a
# flag, so neither is published -- and neither is prose either, which is why the presence of one
# must not make the whole literal read as a description and end the scan.
CLI_FLAG_ARG = re.compile(r"^[<\[][A-Za-z][\w.-]*[>\]]$")

seen_cmds = set()
for p in walk(exts={".ts", ".js", ".py", ".go"}):
    r = rel(p)
    lines = read(p).splitlines()
    for i, line in enumerate(lines, 1):
        names = [m.group(1).split()[0] for m in SUBCOMMAND.finditer(line)]
        for m in CLI_FLAG_DECL.finditer(line):
            # The leading comma is the one the call's own `(` does not write, so a declaration
            # that opens with a keyword -- `.option(name="--x")` -- is cut at its first argument
            # by the same expression rather than by a second one spelled slightly differently.
            args = "," + line[m.end():]
            # Black moves the arguments off the call line once the call passes 88 columns:
            # first onto one indented line together, and one to a line only where they do not
            # fit that way either. Both shapes are ordinary for an add_argument carrying an
            # action and a help sentence, and both leave the call line holding no literal.
            # Reading one line at a time saw `parser.add_argument(` and no literal at all, so a
            # formatted project declared no flags whatsoever and doctype.py had nothing to
            # suppress a `--verbose` on a user page with: the WEBSTER_SURVEY path was inert for
            # exactly the projects that run a formatter. The lines are accumulated the way the
            # decorator pass above accumulates them and under the same six-line cap, and the
            # cap is there for the same reason: it stops a `(` that opened something else from
            # swallowing the lines beneath it. Hitting the cap costs a flag rather than
            # inventing one, which is the direction this list is allowed to fail in.
            #
            # The completion test is not the same one, and this comment claimed it was. The
            # decorator pass counts through call_depth_delta, which skips the parens inside a
            # `#` comment or a string literal; the count here is over the raw text, so a `)`
            # in either place ends the join early. Nothing a reader sees turns on that,
            # because CLI_ARG_END cuts the literal scan at the first `)` or `#` whatever
            # stopped the join -- those are the very characters the two counts disagree about,
            # so the text past one is never read here either way. The anchor stays `i`, the
            # line the call opens on, because that is the line a reader is sent to.
            if args.count("(") + 1 > args.count(")"):
                for cont in lines[i:i + 6]:
                    args += " " + cont.strip()
                    if args.count("(") + 1 <= args.count(")"):
                        break
            stop = CLI_ARG_END.search(args)
            for lit in CLI_STRING.findall(args[:stop.start()] if stop else args):
                tokens = [t for t in re.split(r"[,\s]+", lit.strip()) if t]
                flags = [t for t in tokens if CLI_FLAG_TOKEN.match(t)]
                if not flags or not all(CLI_FLAG_TOKEN.match(t) or CLI_FLAG_ARG.match(t)
                                        for t in tokens):
                    break
                names += flags
        for v in names:
            if v in seen_cmds:
                continue
            seen_cmds.add(v)
            user_surface["commands"].append({"name": v, "anchor": f"{r}:{i}"})

# Screens, from more than one convention. surface["pages"] only knows Next.js App Router, and
# a real audit of a 46-screen React app came back with zero screens, which made the coverage
# half of the audit unavailable without anyone being told it was missing.
screens = list(surface.get("pages", []))
seen_screens = {s["path"] for s in screens}


def add_screen(path, anchor, name=None):
    if path in seen_screens:
        return
    seen_screens.add(path)
    entry = {"path": path, "anchor": anchor}
    if name:
        entry["name"] = name
    screens.append(entry)


# Router config: <Route path="/deployments" element={<DeploymentsPage />} />, and the file-based
# equivalents. The declared path is what a person sees in the address bar.
for p in walk(exts={".tsx", ".jsx", ".ts", ".js", ".vue", ".svelte"}):
    r = rel(p)
    if re.search(r"(^|/)(test|tests|__tests__|stories)/|\.(test|spec|stories)\.", r):
        continue
    for i, line in enumerate(read(p).splitlines(), 1):
        for m in re.finditer(r"""<Route\b[^>]*\bpath\s*=\s*["']([^"']+)["']""", line):
            add_screen(m.group(1), f"{r}:{i}")
        for m in re.finditer(r"""\bpath\s*:\s*["'](/[^"']*)["']""", line):
            add_screen(m.group(1), f"{r}:{i}")

# Component-per-screen, which is the convention when the router is generated or centralised.
# A file named FooPage.tsx under a pages/ or views/ directory is a screen whatever routes it.
for p in walk(exts={".tsx", ".jsx", ".vue", ".svelte"}):
    r = rel(p)
    if re.search(r"(^|/)(test|tests|__tests__|stories)/|\.(test|spec|stories)\.", r):
        continue
    base = os.path.splitext(os.path.basename(p))[0]
    in_screen_dir = re.search(r"(^|/)(pages|views|screens|routes)/", r)
    # Only the screen itself. A Dialog, Form, Modal or Card under pages/ is a part of a screen,
    # and counting them made a 46-screen app report 113.
    if not (in_screen_dir and base.endswith(("Page", "View", "Screen"))):
        continue
    name = re.sub(r"(Page|View|Screen)$", "", base)
    label = re.sub(r"(?<!^)(?=[A-Z])", " ", name).strip()
    add_screen("component:" + base, f"{r}:1", name=label)

user_surface["screens"] = screens
for k in ("labels", "messages"):
    user_surface[k] = user_surface[k][:120]

# ---------------------------------------------------------------- tooling
tooling = []
if "typescript" in stack:
    if surface["exports"]:
        tooling.append({"tool": "typedoc", "for": "exported types and TSDoc",
                        "install": "npm i -D typedoc", "run": "npx typedoc --out docs/api src/index.ts"})
        tooling.append({"tool": "@microsoft/api-extractor", "for": "public API surface report, catches breaking changes",
                        "install": "npm i -D @microsoft/api-extractor", "run": "npx api-extractor run --local"})
    else:
        tooling.append({"tool": "typedoc", "for": "exported types (no package exports declared, scope it by hand)",
                        "install": "npm i -D typedoc", "run": "npx typedoc --out docs/api src"})
if surface["specs"]:
    tooling.append({"tool": "scalar", "for": "render the existing OpenAPI spec",
                    "install": "npm i @scalar/api-reference", "run": "see @scalar/api-reference docs"})
elif surface["http"]:
    tooling.append({"tool": "none", "for": "no OpenAPI spec found; the HTTP surface below is the source of truth",
                    "install": "", "run": ""})
if "python" in stack:
    tooling.append({"tool": "mkdocstrings", "for": "Python API reference from docstrings",
                    "install": "pip install mkdocstrings[python]", "run": "mkdocs build"})
if "go" in stack:
    tooling.append({"tool": "godoc", "for": "Go package reference", "install": "", "run": "go doc ./..."})

# ---------------------------------------------------------------- tests
test_files = [rel(p) for p in walk(exts={".ts", ".tsx", ".js", ".py", ".go"})
              if re.search(r"(^|/)(test|tests|__tests__)/|\.(test|spec)\.", rel(p))]

print(json.dumps({
    "root": ROOT,
    "name": pkg.get("name") or py_meta.get("name") or os.path.basename(ROOT),
    "description": pkg.get("description") or py_meta.get("description") or "",
    "stack": stack,
    "frameworks": frameworks,
    "surface": surface,
    "user_surface": user_surface,
    "tooling": tooling,
    "tests": {"count": len(test_files), "files": sorted(test_files)[:40]},
    "existing_docs": sorted(rel(p) for p in walk(exts={".md"}) if "node_modules" not in rel(p))[:40],
}, indent=2))
