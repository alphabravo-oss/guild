"""Find a project's build, lint and test commands, and the spec's
integration or end-to-end check, from files alone.

Detection reads files only; it runs nothing. Each command is
``{"cmd", "cwd", "why"}`` with ``cwd`` relative to the project root. The root
and each immediate subdirectory are checked, so a repo with ``web/`` and
``api/`` gets both. A Makefile is used only where no language marker is
found, since a Makefile target can mean anything. (Copied from joiner.)

``integration`` is trowel's: it reads the spec for a command on a line that
talks about an integration, end-to-end, acceptance or smoke check, or about
Docker, or that is a ``Verification:`` line, backticked or written bare after
"run", and skips the project's unit-test command, which such lines often name
for contrast ("unit tests run with `mage test`"). It also reads the
repository for the targets that run such checks (Makefile, magefile,
package.json scripts, justfile). It also names what the check needs before it
can start here: its runner on PATH, a Docker daemon when its definition uses
one, and a dry start that compiles or lists it without running it. When the
spec talks about such a check but no command can be matched, it returns
nothing and the workflow asks one cheap agent to name it.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from common import SKIP_DIRS  # noqa: E402


def _cmd(cmd: str, cwd: str, why: str) -> dict:
    return {"cmd": cmd, "cwd": cwd, "why": why}


def _node(d: Path, rel: str) -> dict:
    try:
        pkg = json.loads((d / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    scripts = pkg.get("scripts") or {}
    pm = ("pnpm" if (d / "pnpm-lock.yaml").exists() else "yarn" if (d / "yarn.lock").exists()
          else "bun" if (d / "bun.lockb").exists() or (d / "bun.lock").exists() else "npm")
    out = {"build": [], "lint": [], "test": []}
    why = f"{rel or '.'}/package.json scripts"
    if "build" in scripts:
        out["build"].append(_cmd(f"{pm} run build", rel, why))
    for name in ("lint", "typecheck", "type-check", "check-types"):
        if name in scripts:
            out["lint"].append(_cmd(f"{pm} run {name}", rel, why))
    test = scripts.get("test", "")
    if test and "no test specified" not in test:
        out["test"].append(_cmd(f"{pm} test", rel, why))
    return out


def _python(d: Path, rel: str) -> dict:
    text = ""
    for name in ("pyproject.toml", "setup.cfg", "tox.ini"):
        if (d / name).exists():
            text += (d / name).read_text(encoding="utf-8", errors="replace")
    out = {"build": [], "lint": [], "test": []}
    if "pytest" in text or (d / "tests").is_dir() or (d / "conftest.py").exists():
        out["test"].append(_cmd("python3 -m pytest -q", rel, "pytest configured or tests/ present"))
    if "[tool.ruff" in text or (d / "ruff.toml").exists():
        out["lint"].append(_cmd("ruff check .", rel, "ruff configured"))
    if "[tool.mypy" in text or (d / "mypy.ini").exists():
        out["lint"].append(_cmd("mypy .", rel, "mypy configured"))
    return out


def _makefile(d: Path, rel: str) -> dict:
    text = (d / "Makefile").read_text(encoding="utf-8", errors="replace")
    targets = set(re.findall(r"^([A-Za-z][\w.-]*)\s*:(?!=)", text, re.MULTILINE))
    out = {"build": [], "lint": [], "test": []}
    for kind, names in (("build", ("build",)), ("lint", ("lint", "vet", "check")), ("test", ("test",))):
        for n in names:
            if n in targets:
                out[kind].append(_cmd(f"make {n}", rel, f"Makefile target {n}"))
                break
    return out


def _detect_dir(d: Path, rel: str) -> dict:
    found = []
    if (d / "go.mod").exists():
        found.append({"build": [_cmd("go build ./...", rel, "go.mod")],
                      "lint": [_cmd("go vet ./...", rel, "go.mod")],
                      "test": [_cmd("go test ./...", rel, "go.mod")]})
    if (d / "package.json").exists():
        found.append(_node(d, rel))
    if (d / "Cargo.toml").exists():
        found.append({"build": [_cmd("cargo build --all-targets", rel, "Cargo.toml")],
                      "lint": [_cmd("cargo clippy --all-targets", rel, "Cargo.toml, cargo-clippy installed")]
                      if shutil.which("cargo-clippy") else [],
                      "test": [_cmd("cargo test", rel, "Cargo.toml")]})
    if any((d / n).exists() for n in ("pyproject.toml", "setup.py", "setup.cfg")):
        found.append(_python(d, rel))
    if (d / "pom.xml").exists():
        found.append({"build": [_cmd("mvn -q -DskipTests package", rel, "pom.xml")], "lint": [],
                      "test": [_cmd("mvn -q test", rel, "pom.xml")]})
    elif (d / "build.gradle").exists() or (d / "build.gradle.kts").exists():
        gw = "./gradlew" if (d / "gradlew").exists() else "gradle"
        found.append({"build": [_cmd(f"{gw} assemble", rel, "build.gradle")], "lint": [],
                      "test": [_cmd(f"{gw} test", rel, "build.gradle")]})
    if list(d.glob("*.sln")) or list(d.glob("*.csproj")):
        found.append({"build": [_cmd("dotnet build", rel, "*.sln/*.csproj")], "lint": [],
                      "test": [_cmd("dotnet test", rel, "*.sln/*.csproj")]})
    if not found and (d / "Makefile").exists():
        found.append(_makefile(d, rel))
    merged = {"build": [], "lint": [], "test": []}
    for f in found:
        for k in merged:
            merged[k].extend(f.get(k, []))
    return merged


def detect(root: Path) -> dict:
    root = Path(root)
    out = _detect_dir(root, "")
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        if d.name in SKIP_DIRS or d.name.startswith("."):
            continue
        sub = _detect_dir(d, d.name)
        for k in out:
            out[k].extend(sub[k])
    return out


# ---------------------------------------------------------------------------
# The integration or end-to-end check
# ---------------------------------------------------------------------------
INTEG_WORDS = re.compile(r"\b(e2e|end[- ]to[- ]end|integration|acceptance|smoke)\b", re.IGNORECASE)
RUNNERS = ("make", "mage", "npm", "pnpm", "yarn", "bun", "npx", "just", "task", "go", "pytest", "python", "python3",
           "cargo", "mvn", "gradle", "./gradlew", "dotnet", "bash", "sh", "docker", "tox", "nox", "uv")
NEEDS_DOCKER = re.compile(r"docker|testcontainers|\bkind\b|compose|podman", re.IGNORECASE)
SPAN = re.compile(r"`([^`\n]+)`")
BARE = re.compile(r"(?:\b(?:run|runs|running)|verification:)[\s*_]+((?:make|mage|just|task)\s+[a-z][\w:.-]*)", re.IGNORECASE)
VERIFY_LINE = re.compile(r"\bverification:", re.IGNORECASE)
UNIT_NAMES = {"test", "tests", "unit", "unittest", "check"}


def _make_targets(d: Path, rel: str) -> list[dict]:
    f = d / "Makefile"
    if not f.is_file():
        return []
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    for i, line in enumerate(lines):
        m = re.match(r"^([A-Za-z][\w.-]*)\s*:(?!=)", line)
        if m:
            body = [line] + [x for x in lines[i + 1:i + 40] if x.startswith("\t")]
            out.append({"cmd": f"make {m.group(1)}", "cwd": rel, "name": m.group(1), "why": f"{rel or '.'}/Makefile target {m.group(1)}",
                        "text": "\n".join(body)})
    return out


def _mage_targets(d: Path, rel: str) -> list[dict]:
    files = [d / "magefile.go"] if (d / "magefile.go").is_file() else sorted((d / "magefiles").glob("*.go"))
    out = []
    for f in files:
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"^func ([A-Z]\w*)\(\)\s*(?:error\s*)?\{", text, re.MULTILINE):
            name = m.group(1)[0].lower() + m.group(1)[1:]
            body = text[m.start():text.find("\n}", m.start()) + 2]
            out.append({"cmd": f"mage {name}", "cwd": rel, "name": name, "why": f"{f.relative_to(d.parent if rel else d)} func {m.group(1)}",
                        "text": body})
    return out


def _script_targets(d: Path, rel: str) -> list[dict]:
    out = []
    try:
        pkg = json.loads((d / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pkg = {}
    pm = ("pnpm" if (d / "pnpm-lock.yaml").exists() else "yarn" if (d / "yarn.lock").exists()
          else "bun" if (d / "bun.lockb").exists() or (d / "bun.lock").exists() else "npm")
    for name, body in (pkg.get("scripts") or {}).items():
        out.append({"cmd": f"{pm} run {name}", "cwd": rel, "name": name, "why": f"{rel or '.'}/package.json script {name}", "text": str(body)})
    jf = d / "justfile"
    if jf.is_file():
        for m in re.finditer(r"^([A-Za-z][\w-]*)[^:\n]*:(?!=)", jf.read_text(encoding="utf-8", errors="replace"), re.MULTILINE):
            out.append({"cmd": f"just {m.group(1)}", "cwd": rel, "name": m.group(1), "why": f"{rel or '.'}/justfile recipe {m.group(1)}", "text": m.group(0)})
    return out


def targets(root: Path) -> list[dict]:
    """Every command a project defines as a named target, at the root and one level down."""
    root = Path(root)
    dirs = [(root, "")] + [(d, d.name) for d in sorted(p for p in root.iterdir() if p.is_dir())
                           if d.name not in SKIP_DIRS and not d.name.startswith(".")]
    out = []
    for d, rel in dirs:
        out += _make_targets(d, rel) + _mage_targets(d, rel) + _script_targets(d, rel)
    return out


def _norm_cmd(cmd: str) -> str:
    c = " ".join(cmd.split()).lower()
    return re.sub(r"^(npm|pnpm|yarn|bun) (?:run )?", r"\1 run ", c)


def _spec_commands(spec_text: str) -> list[str]:
    """Commands on spec lines that talk about an integration, end-to-end or Docker check, or verify one.

    A command is backticked, or written bare after "run" or "Verification:"
    when its runner is a task runner (``Run mage metrics: it boots…``)."""
    out = []
    for line in spec_text.splitlines():
        if not (INTEG_WORDS.search(line) or NEEDS_DOCKER.search(line) or VERIFY_LINE.search(line)):
            continue
        spans = [s.strip() for s in SPAN.findall(line)] + [m.group(1).rstrip(":.") for m in BARE.finditer(SPAN.sub("", line))]
        for span in spans:
            if span.split(" ")[0] in RUNNERS and len(span.split()) >= 2 and span not in out:
                out.append(span)
    return out


def _is_unit_test(cmd: str, unit: set[str]) -> bool:
    """The project's unit-test command, or a task-runner target named like one: not an integration check."""
    words = _norm_cmd(cmd).split()
    if _norm_cmd(cmd) in unit:
        return True
    return len(words) == 2 and words[0] in ("make", "mage", "just", "task") and words[1] in UNIT_NAMES


def _paths_in(cmd: str) -> list[str]:
    return [t for t in cmd.split()[1:] if "/" in t and not t.startswith("-")]


def describe(root: Path, cmd: str, cwd: str = "", why: str = "", text: str = "", exists: bool = True) -> dict:
    """An integration check with what it needs before it can start here."""
    root = Path(root)
    words = cmd.split()
    runner = words[0] if words else ""
    needs = []
    if runner and not runner.startswith(("./", "/")) and runner not in ("bash", "sh"):
        needs.append({"what": f"{runner} on PATH", "command": f"command -v {runner}"})
    elif runner.startswith("./"):
        needs.append({"what": f"{runner} exists", "command": f"test -x {runner}"})
    body = text
    for p in _paths_in(cmd):
        d = root / cwd / p.replace("/...", "").rstrip("/")
        files = [d] if d.is_file() else (list(d.rglob("*"))[:400] if d.is_dir() else [])
        for f in files:
            if f.is_file() and f.stat().st_size < 500_000:
                body += f.read_text(encoding="utf-8", errors="replace")
    if NEEDS_DOCKER.search(body):
        needs.append({"what": "a running Docker daemon (the check's definition uses containers)", "command": "docker info"})
    dry = "mage -l" if runner == "mage" else None
    if exists:
        if runner == "make" and len(words) > 1:
            dry = f"make -n {words[1]}"
        elif runner == "go" and len(words) > 1 and words[1] == "test":
            tags = next((w for w in words if w.startswith("-tags")), "")
            tagval = words[words.index(tags) + 1] if tags == "-tags" and words.index(tags) + 1 < len(words) else ""
            pkgs = [w for w in words[2:] if w.startswith(("./", "."))] or ["./..."]
            dry = f"go vet {('-tags ' + tagval + ' ') if tagval else (tags + ' ' if tags else '')}{' '.join(pkgs)}"
        elif runner in ("pytest",) or (runner in ("python", "python3") and "pytest" in words):
            dry = f"{cmd} --collect-only -q"
    return {"command": cmd, "cwd": cwd, "source": why, "exists": exists, "needs": needs, "dry_run": dry}


def integration(root: Path, spec_text: str) -> dict:
    """{"found": dict|None, "asked": bool}: the check the spec asks for, if code can name it.

    asked is False when the spec never mentions an integration, end-to-end,
    acceptance or smoke check; then there is nothing to find."""
    asked = bool(INTEG_WORDS.search(spec_text))
    if not asked:
        return {"found": None, "asked": False}
    tg = targets(root)
    by_cmd = {_norm_cmd(t["cmd"]): t for t in tg}
    unit = {_norm_cmd(c["cmd"]) for c in detect(root)["test"]}
    named = [c for c in _spec_commands(spec_text) if not _is_unit_test(c, unit)]
    for c in named:
        t = by_cmd.get(_norm_cmd(c))
        if t:
            return {"found": describe(root, t["cmd"], t["cwd"], f"the spec names `{c}`; {t['why']}", t["text"]), "asked": True}
    # A task-runner command whose target the repo does not define yet: the name alone
    # (`make test-integration`) does not prove the check exists, so it falls through
    # to the "build writes it" case rather than being dry-started against a missing target.
    runners = ("make", "mage", "just", "npm", "pnpm", "yarn", "bun")
    for c in named:
        if any((Path(root) / p.replace("/...", "").rstrip("/")).exists() for p in _paths_in(c)) or (
                INTEG_WORDS.search(c) and c.split()[0] not in runners):
            return {"found": describe(root, c, "", f"the spec names `{c}`"), "asked": True}
    # Prefer a target whose name says what it checks over a dev loop or composite gate
    # named earlier on the same lines (`make dev-up` before `make test-integration`).
    for c in sorted(named, key=lambda c: not INTEG_WORDS.search(c)):
        if c.split()[0] in runners:
            # A suite the build will write: it will run like the project's other such suites.
            kin = "\n".join(t["text"] for t in tg if INTEG_WORDS.search(t["name"]) and t["cmd"].split()[0] == c.split()[0])
            return {"found": describe(root, c, "", f"the spec names `{c}`; no such target exists yet, so the build writes it",
                                      kin, exists=False), "asked": True}
    hits = [t for t in tg if INTEG_WORDS.search(t["name"])]
    if len(hits) == 1:
        t = hits[0]
        return {"found": describe(root, t["cmd"], t["cwd"], f"{t['why']} (the spec asks for such a check and names no command)",
                                  t["text"]), "asked": True}
    return {"found": None, "asked": True}
