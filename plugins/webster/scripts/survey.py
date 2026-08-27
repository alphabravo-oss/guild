#!/usr/bin/env python3
"""Detect the stack and enumerate the real public surface of a repo.

Prints JSON. Every entry carries a file:line anchor, because a surface item
without an anchor is a claim and this script is not allowed to make claims.
"""
import json, os, re, subprocess, sys

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
    try:
        return json.loads(read(p))
    except Exception:
        return {}


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
deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
stack, frameworks = [], []

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
if "python" in stack:
    for p in walk(exts={".py"}):
        for hit in anchors(p, r"@\w+\.(get|post|put|patch|delete|route)\s*\(\s*['\"]([^'\"]+)"):
            m = re.search(r"\.(get|post|put|patch|delete|route)\s*\(\s*['\"]([^'\"]+)", hit["value"])
            if m:
                surface["http"].append({"method": m.group(1).upper(), "path": m.group(2),
                                        "anchor": hit["anchor"], "source": "python-decorator"})

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

# Env vars and specs
for envf in (".env.example", ".env.sample", ".env.template"):
    p = os.path.join(ROOT, envf)
    if os.path.exists(p):
        surface["config"] += [{"name": h["value"], "anchor": h["anchor"]}
                              for h in anchors(p, r"^([A-Z][A-Z0-9_]+)=", 1)]
# Env vars read from code. .env.example is what someone remembered to write down;
# this is what the code actually reads, and the difference is usually the finding.
declared = {c["name"] for c in surface["config"]}
for p in walk(exts={".ts", ".tsx", ".js", ".mjs", ".py", ".go"}):
    for hit in anchors(p, r"""(?:process\.env\.([A-Z][A-Z0-9_]+)|process\.env\[["']([A-Z][A-Z0-9_]+)|env\[["']([A-Z][A-Z0-9_]+)|os\.environ(?:\.get)?[\[(]["']([A-Z][A-Z0-9_]+)|os\.Getenv\(["']([A-Z][A-Z0-9_]+))"""):
        name = next((g for g in re.search(
            r"""(?:process\.env\.([A-Z][A-Z0-9_]+)|process\.env\[["']([A-Z][A-Z0-9_]+)|env\[["']([A-Z][A-Z0-9_]+)|os\.environ(?:\.get)?[\[(]["']([A-Z][A-Z0-9_]+)|os\.Getenv\(["']([A-Z][A-Z0-9_]+))""",
            hit["value"]).groups() if g), None)
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
seen_msgs = set()
for p in walk(exts={".ts", ".tsx", ".js", ".jsx", ".py", ".go", ".rs"}):
    r = rel(p)
    if re.search(r"(^|/)(test|tests|__tests__)/|\.(test|spec)\.", r):
        continue
    for i, line in enumerate(read(p).splitlines(), 1):
        for m in re.finditer(r"""(?:Error|error|throw|toast|notify|message)[^"'\n]{0,30}["']([A-Z][^"']{12,110})["']""", line):
            v = m.group(1).strip()
            if v.lower() in seen_msgs or "{" in v:
                continue
            seen_msgs.add(v.lower())
            user_surface["messages"].append({"text": v, "anchor": f"{r}:{i}"})

# Subcommands a person types, which is the user surface of anything without a screen.
seen_cmds = set()
for p in walk(exts={".ts", ".js", ".py", ".go"}):
    r = rel(p)
    for i, line in enumerate(read(p).splitlines(), 1):
        for m in re.finditer(
                r"""(?:\.command\(|add_parser\(|Use:\s*)["']([a-z][a-z0-9 :_-]{1,40})["']""", line):
            v = m.group(1).split()[0]
            if v in seen_cmds:
                continue
            seen_cmds.add(v)
            user_surface["commands"].append({"name": v, "anchor": f"{r}:{i}"})

user_surface["screens"] = surface.get("pages", [])
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
    "name": pkg.get("name") or os.path.basename(ROOT),
    "description": pkg.get("description", ""),
    "stack": stack,
    "frameworks": frameworks,
    "surface": surface,
    "user_surface": user_surface,
    "tooling": tooling,
    "tests": {"count": len(test_files), "files": sorted(test_files)[:40]},
    "existing_docs": sorted(rel(p) for p in walk(exts={".md"}) if "node_modules" not in rel(p))[:40],
}, indent=2))
