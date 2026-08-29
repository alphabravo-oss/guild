#!/usr/bin/env python3
"""Drift detection for documentation, on the openwiki model.

  record [docs]   store HEAD, a hash of the docs tree, every anchor the docs cite, and a hash
                  of each cited line that resolves
  check  [docs]   report what changed since the last record, and which anchors no longer resolve

Statuses and exit codes for check: clean and unrelated_changes exit 0, drift exits 1, and
no_docs, no_manifest, no_anchors, no_git, head_missing and hashes_partial exit 2. An anchor
that no longer resolves is drift, and drift is a P0, on any run that had a set to check.
Where the set could not be checked the status says that first. Five statuses take the exit
code ahead of drift, not the three this paragraph used to name: no_docs, no_manifest,
no_anchors, no_git and head_missing. Of the two it left out only no_manifest is reachable with a
broken anchor in hand — a tree whose manifest is missing or unreadable resolves every anchor it
has, lists the broken ones, and still reports no_manifest rather than drift — so the run this
paragraph described least well was the run that had a finding in it. The reason is one reason
for all five: "nothing here could be checked" is a different claim from "this was checked and
it is wrong". hashes_partial is the one exit-2 status that sits below drift instead, for the
reason its own paragraph below gives. A broken anchor found under the four of the five that
reach the anchors half — no_manifest, no_anchors, no_git and head_missing — is still listed in
broken_anchors beside the not-checked status rather than dropped, because the anchors half
runs whether or not git can answer. no_docs is the fifth, and it returns before a page is
read, so its envelope carries no anchor field at all. `clean` is reserved for a set where
nothing changed at all: code that changed but that no page cites is `unrelated_changes`,
because a gate that fails on ordinary development teaches the reader to stop reading the gate.
`hashes_partial` is the same refusal for the line half: an anchor the record itself held that
resolves but carries no recorded digest was never compared, and a run holding one has not
earned exit 0. It is the one not-checked status that sits BELOW drift, being the narrower
claim: some anchors were measured and some were not, so a finding the run did make is still
reported as a finding. A citation a page grew after the record is not one of those — there was
nothing to take a digest of — and it is reported as the docs edit it is.

The docs directory is the second argument, or WEBSTER_DOCS, or "docs". It was env-var only
until a real audit pointed the command at a repo whose docs live in `documentation/`, got
"no_manifest" for a tree that had one, and reported a wrong answer that looked like a right one.
"""
import hashlib, json, os, re, subprocess, sys

ROOT = os.path.abspath(os.environ.get("WEBSTER_ROOT", "."))
_DOCS_ARG = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("WEBSTER_DOCS", "docs")
DOCS = _DOCS_ARG if os.path.isabs(_DOCS_ARG) else os.path.join(ROOT, _DOCS_ARG)
MANIFEST = os.path.join(DOCS, ".webster.json")
# Only real source/config extensions. Without this, "127.0.0.1:3000" reads as an anchor.
SRC_EXT = ("ts|tsx|js|jsx|mjs|cjs|py|go|rs|rb|java|kt|swift|c|h|cc|cpp|cs|php|sh|bash|zsh"
           "|sql|css|scss|html|vue|svelte|astro|json|jsonc|yaml|yml|toml|ini|env|md|mdx|txt|lock")
# A leading \b cannot match before a dot, so a dotfile citation such as `.air.server.toml:9`
# was read as `air.server.toml:9` and never resolved. Anchor on the preceding character
# instead, which admits a leading dot without matching mid-word.
ANCHOR = re.compile(rf"(?<![\w./-])([\w./-]+\.(?:{SRC_EXT})):(\d+)\b")

NO_HEAD_NOTE = ("git could not resolve HEAD here, so this manifest records no commit; the next "
                "check still resolves the anchors and compares the recorded line digests, and "
                "reports no_git for the one half it has no commit to start the diff from")


class GitUnavailable(Exception):
    """git could not answer the question that was asked.

    Every failure used to become an empty string, and an empty string reads exactly like
    "nothing changed" — but not identically for the three failures this covers, and the
    difference is why they end at exit 2 rather than at a tidier exit 0. Replayed against the
    pre-change script: a tree with no git reported `clean` at exit 0, and so did a recorded HEAD
    that had been rebased away, whose `diff OLD..HEAD` exited 128 into that empty string. A
    repository with no commits reported `drift` at exit 1 instead — `status --short` lists every
    file as `??` where nothing has been committed yet, so the cited file was read as dirty and
    its page went suspect.

    That third one is not the counter-example it looks like. It is the same defect under the
    opposite sign: an exit code describing git's state rather than the pages', which would
    report drift on a set whose every page was correct. A false pass and a finding nobody can
    act on both end with the reader trusting the gate less than the page, and neither is an
    answer, so a git that cannot answer now says so instead.
    """


def git(*args):
    """Raw stdout from one git command, or GitUnavailable.

    The result is deliberately NOT stripped. `git status --porcelain` puts the two status
    letters in columns 1-2 and the path from column 4, so a leading space is data. str.strip()
    takes the leading whitespace off the whole string and not off each line, so exactly one
    record ever shifted left by one — the first — and every record after it sliced correctly.
    One was enough. Measured against the fixture, the first record is ` M src/app/main.py`,
    untracked entries sorting after the modified ones; the slice that followed cut it down to
    `rc/app/main.py`, no anchor matched it, and an uncommitted edit to a cited file reported
    clean. A defect that mangles only the first record is not a smaller defect than one that
    mangles all of them — it is one that disappears from any test where the cited file happens
    to sort second. Callers that want one value strip it themselves.

    The decoding is pinned here rather than left to `text=True`, which decodes with the locale's
    preferred encoding. An earlier version of this comment named "US-ASCII under LC_ALL=C" as
    the failing condition, and that condition is the one case that cannot fail: LC_ALL=C is
    exactly what PEP 538 coerces to C.UTF-8 and what PEP 540 turns UTF-8 mode on for, so CPython
    hands the run a UTF-8 preferred encoding and every byte decodes. The real trigger is a
    non-UTF-8 locale with neither of those in effect — PYTHONCOERCECLOCALE=0 and PYTHONUTF8=0,
    or a minimal image carrying no C.UTF-8 to coerce to, which is the pair
    test_non_ascii_path_under_a_c_locale_still_answers sets to reach it. There the preferred
    encoding really is US-ASCII, and the first non-ASCII byte of a path in the porcelain output
    raised UnicodeDecodeError. That is a ValueError, not one of the errors caught below, so it
    left main() as a traceback at exit 1 — the code that means drift, handed to a caller when
    nothing had been measured at all. surrogateescape decodes any byte git can print, and UTF-8
    is what the pages themselves were read as, so a path git names still equals the path an
    anchor cites.
    """
    try:
        return subprocess.run(["git", "--no-pager", *args], cwd=ROOT, check=True,
                              capture_output=True, encoding="utf-8",
                              errors="surrogateescape").stdout
    except (OSError, subprocess.SubprocessError) as e:
        raise GitUnavailable(f"git {' '.join(args)}: {e}") from e


def head_sha():
    """The current HEAD, or None when git cannot say what it is.

    None covers all three of git missing, this not being a repository, and a repository with no
    commits yet, because from here they are the same fact: the code half cannot be measured.
    """
    try:
        return git("rev-parse", "HEAD").strip()
    except GitUnavailable:
        return None


def commit_exists(sha):
    """Whether `sha` is a commit this repository still has.

    `git diff --name-only OLD..HEAD` exits 128 when OLD is gone, which is why this runs before
    the diff rather than after it; `rev-parse --verify --quiet` is the silent existence test.
    It only runs once HEAD has already resolved, so a failure here means the recorded commit is
    missing rather than git being broken.

    Reachability is deliberately NOT the question. `git reset --hard HEAD~1` — the trigger
    AC-004 and OT-004 name — does not make the recorded commit missing: the object is still in
    the store and the reflog still points at it, so rev-parse finds it and this returns True.
    Only expiring the reflog and pruning removes it. FR-006 is Locked on the definition
    ("recorded gitHead not found: `git rev-parse --verify --quiet SHA^{commit}` fails"), so
    that is what head_missing means here; the AC-004 wording describes the everyday first step
    towards that state rather than stating a second definition. A test for head_missing has to
    expire and prune, and test_rebased_away_head_reports_head_missing does.
    """
    try:
        git("rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        return True
    except GitUnavailable:
        return False


def porcelain_paths(out):
    """Every path named by `git status --porcelain -z`, renames and copies included.

    A -z record is `XY<space>path` terminated by NUL, and a rename or copy adds a second field
    holding the path the entry came FROM, after the one it went TO. Both halves matter: a page
    citing src/app/main.py must go suspect when that file is renamed away, and it is the old
    path the anchor names. -z is also the only format with no quoting to unescape, so a path
    with a space or a non-ASCII byte arrives whole instead of arriving as "\\303\\251".
    """
    fields = out.split("\0")
    paths = []
    i = 0
    while i < len(fields):
        record = fields[i]
        i += 1
        if len(record) < 4:
            continue  # the empty field after the final NUL
        paths.append(record[3:])
        if "R" in record[:2] or "C" in record[:2]:
            if i < len(fields) and fields[i]:
                paths.append(fields[i])
            i += 1
    return paths


def repo_prefix():
    """Where ROOT sits inside its git repository, as a prefix ending in "/", or "".

    git names every path in `status --porcelain` relative to the repository root, never to the
    directory the command ran in: --porcelain is documented immune to status.relativePaths, and
    it holds under color.status=always too. Anchors, pages and
    the docs directory are all relative to WEBSTER_ROOT. Those are the same namespace only
    when WEBSTER_ROOT is itself the repository root, which is the layout this file was written
    in and is not the layout of an ordinary monorepo. With the project one directory down, git
    said `site/src/app/main.py` where the anchor said `src/app/main.py`, the two never
    intersected, suspect_pages came back empty on every run, and `site/docs/.webster.json`
    failed the inside-docs test and was counted as a changed code file — so an uncommitted edit
    to a cited file printed unrelated_changes at exit 0 with a note reading "no page cites any
    of them", with the page sitting in the tree citing it. Resolve the prefix once and put
    git's answers into the pages' namespace before anything is compared.

    `diff` needs one thing more, which an earlier version of this comment denied: it said "diff
    has no such setting at all", and `diff.relative` is exactly such a setting. Set true in a
    developer's ~/.gitconfig it makes `diff --name-only` name paths from the directory the
    command ran in, so with the docs one level down the diff handed under_root()
    `src/app/main.py` where this prefix is `site/`; every path was dropped as living outside
    ROOT, `changed` came back empty, and a COMMITTED edit to a cited file printed clean at
    exit 0 — US-001 negated outright by a line of configuration no repository under test
    carries, because the suite pins GIT_CONFIG_GLOBAL at os.devnull. That false comment is why
    no guard existed. The diff call pins --no-relative; the flag and the config arrived in the
    same git release, so a git too old for the flag has no config to override.
    """
    return git("rev-parse", "--show-prefix").strip()


def repo_root_of(prefix):
    """The repository root, as an absolute path, derived from the prefix git printed.

    Derived from ROOT and the prefix rather than read back from `rev-parse --show-toplevel`,
    because that answer has every symlink resolved while ROOT is os.path.abspath of the
    directory the caller named and keeps its own. On a macOS default, where /tmp is a link to
    /private/tmp, the two spellings of one directory never compare equal, and comparing them
    would put every path in the repository outside it. Counting the prefix's segments does the
    same arithmetic without leaving the caller's namespace.
    """
    if not prefix:
        return ROOT
    return os.path.abspath(
        os.path.join(ROOT, *[os.pardir] * len(prefix.strip("/").split("/"))))


def in_repo_space(target, repo_root):
    """One anchor's target as git names it: relative to the repository root.

    Anchors are written relative to WEBSTER_ROOT and git names every path relative to the
    repository root, so comparing the two spellings directly is right only where those are the
    same directory. normpath collapses the `../` of a citation that reaches out of ROOT, and
    join lets an absolute citation replace ROOT outright, so a file cited either way arrives
    here as the one path git prints for it.

    A target outside the repository comes back with a leading `../`, which no path in
    `git status` or `git diff` output can equal, so it matches nothing and is named nowhere.
    """
    return os.path.relpath(os.path.normpath(os.path.join(ROOT, target)), repo_root)


def under_root(paths, prefix):
    """The paths git named, restated relative to ROOT, with everything outside ROOT dropped.

    This narrowing is for the code-churn COUNT and for nothing else. code_files_changed is a
    number about this docs set's own scope, and a sibling package's ordinary commit is not a
    change in it, so dropping those paths is what keeps the count from reporting the
    monorepo's traffic as this set's.

    It must not be the oracle that decides which pages are suspect, and it was. The docstring
    that justified that read "every anchor resolves through os.path.join(ROOT, target), so no
    anchor can name it, and doc_files() only walks DOCS, so no page can be it", and both
    halves are false. os.path.join does not confine what it returns: join(ROOT,
    "../shared/lib.py") walks out of ROOT and join(ROOT, "/outside/thing.py") discards ROOT
    altogether, and DOCS is itself allowed to be an absolute path outside ROOT. A page citing
    ../shared/lib.py was matched by ANCHOR, collected, resolved and hashed like any other, and
    then the file it names was dropped here — so a committed edit to a line of that file other
    than the cited one printed clean at exit 0. The line half still covered the cited line
    itself, so what the drop took away is exactly the file-touched signal, and it took it away
    for every citation that leaves ROOT. The suspect oracle is built in main() from the paths
    git named BEFORE this call,
    with each anchor put into git's namespace by in_repo_space().
    """
    out = []
    for path in paths:
        if not path:
            continue
        if prefix:
            if not path.startswith(prefix):
                continue
            path = path[len(prefix):]
        out.append(path)
    return out


def code_paths(candidates, inside_docs, docs_paths):
    """The paths git named that are code, rather than part of this docs set.

    A change inside the docs tree is not code churn: docs_edited_since_record is the field that
    reports it, and .webster.json is the file this script itself just wrote. Where the docs
    directory sits below WEBSTER_ROOT one prefix test says all of that.

    It says none of it when the docs directory IS the root — the layout WEBSTER_DOCS="." asks
    for, a repository whose pages sit at the top. os.path.relpath returns "." there, the prefix
    became "./", and nothing git prints is "./"-prefixed, so the test matched no path at all:
    the manifest counted as a changed code file on every run and `clean` was unreachable on any
    tree in that layout. With no prefix left to test, membership is the page set doc_files()
    already walked plus the manifest, which is what the prefix stands for everywhere else.

    Deliberately not the rule for both cases. Applied to an ordinary docs/ directory it would
    turn docs/_category_.json and every image beside a page into code, and an author replacing
    a screenshot would be told code no page cites had changed.

    Like under_root() above, this narrowing belongs to the count alone. A page may cite a code
    sample that lives beside it inside the docs tree, and filtering that file out of the
    suspect oracle left a committed edit to it reporting clean: tree_hash() covers .md and
    .mdx only, so docs_edited_since_record did not see it either, and no field in the envelope
    said anything at all. The oracle reads the paths git named before this call.
    """
    if inside_docs:
        return [p for p in candidates if not p.startswith(inside_docs)]
    return [p for p in candidates if p not in docs_paths]


def doc_files():
    """Every page in the docs tree, .md and .mdx alike.

    .mdx is in SRC_EXT above, so a page may cite one, but the walk that decided what a page IS
    kept only names ending .md. A Docusaurus set written in .mdx was therefore never scanned:
    zero pages, zero anchors, and a check that reported unrelated_changes at exit 0 with a note
    saying no page cited the file that had just changed — with the page sitting in the tree,
    citing it. The same page under two extensions has to reach the same answer.
    """
    out = []
    for dirpath, dirnames, filenames in os.walk(DOCS):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        out += [os.path.join(dirpath, f) for f in filenames if f.endswith((".md", ".mdx"))]
    return sorted(out)


def tree_hash(paths):
    h = hashlib.sha256()
    for p in paths:
        h.update(os.path.relpath(p, ROOT).encode())
        with open(p, "rb") as f:
            h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


def collect_anchors(paths):
    """Every file:line the docs cite, mapped back to the page that cites it.

    Anchors are read from HTML comments and from a frontmatter `sources:` list, never from
    visible prose. A reader of a published page should not see the implementation path a claim
    was checked against; the anchor exists so the claim can be re-verified, and that is a job
    for this script rather than for the reader.
    """
    found = {}
    for p in paths:
        text = open(p, encoding="utf-8", errors="replace").read()
        rel = os.path.relpath(p, ROOT)

        # inline: <!-- src/lib/net.ts:9 --> keeps the anchor beside the claim it supports
        for m in re.finditer(r"<!--(.*?)-->", text, re.S):
            lineno = text[:m.start()].count("\n") + 1
            for target, tline in ANCHOR.findall(m.group(1)):
                found.setdefault(f"{target}:{tline}", []).append(f"{rel}:{lineno}")

        # frontmatter: a sources list, for claims that belong to the page as a whole
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                for target, tline in ANCHOR.findall(text[3:end]):
                    found.setdefault(f"{target}:{tline}", []).append(f"{rel}:1")
    return found


def manifest_shape_error(data):
    """The first field check() cannot use as the type record wrote it, named, or "".

    The set is exactly the four fields check() reads back, and only those:

      gitHead    sliced for the head_missing note and interpolated into a rev-parse argument
      docsHash   compared against a fresh tree digest to decide docs_edited_since_record
      lineHashes looked up by anchor and compared against a fresh line digest
      anchors    the set of anchors the record held, which decides whether a resolvable anchor
                 carrying no digest is one nothing measured or one the pages grew afterwards

    `pages` is written by record and read by nothing, so its type is not tested: refusing a
    manifest over a field that changes no answer would exit 2 with a note saying nothing had
    been compared, on a manifest every comparison could have run against.

    A manifest is a file people edit, merge and generate, so a field holding the wrong type is
    an ordinary state of it, and it fails in two ways. `"gitHead": 17` reached `recorded[:12]`
    in the head_missing note and raised TypeError out of main() at exit 1 having printed
    nothing at all — exit 1 being the code that means drift, so a run that measured nothing
    told its caller the pages disagree with the code. `"docsHash": 5` never raises: it simply
    never equals a fresh digest, so docs_edited_since_record came back true on a tree nobody
    had touched and the run printed unrelated_changes at exit 0. A wrong answer shaped like a
    right one is the same failure as the traceback, and both get the same answer here: exit 2
    with the field named.

    Absence is not an error for any of the four. AC-006 requires a manifest written before
    lineHashes existed to still be checked, and record has not always written every key, so
    only a value of the wrong type is reported.
    """
    head = data.get("gitHead")
    if head is not None and not isinstance(head, str):
        return f"records a gitHead of type {type(head).__name__} where a string or null belongs"
    docs_hash = data.get("docsHash")
    if docs_hash is not None and not isinstance(docs_hash, str):
        return (f"records a docsHash of type {type(docs_hash).__name__} where the tree "
                "digest's string belongs")
    line_hashes = data.get("lineHashes")
    if line_hashes is not None:
        if not isinstance(line_hashes, dict):
            return (f"records a lineHashes of type {type(line_hashes).__name__} where an "
                    "object keyed by anchor belongs")
        for anchor, digest in line_hashes.items():
            if not isinstance(digest, str):
                return (f"records a lineHashes entry for {anchor} of type "
                        f"{type(digest).__name__} where a hex digest belongs")
    recorded_anchors = data.get("anchors")
    if recorded_anchors is not None and not isinstance(recorded_anchors, dict):
        return (f"records an anchors of type {type(recorded_anchors).__name__} where an "
                "object keyed by anchor belongs")
    return ""


def read_manifest():
    """The recorded manifest as a dict, or the reason there is nothing to compare against.

    `json.load(open(MANIFEST))` had no try and no encoding=, so three ordinary states of a
    file people edit and merge — a torn write, a conflict left in place, a path that cannot be
    read — raised out of main() as a traceback at exit 1. Exit 1 is the code that means drift,
    so a run that measured nothing at all told its caller the pages disagree with the code.
    That is the same class as the git errors swallowed one function above, under the opposite
    sign, and FR-006 reserves exit 1 for `drift` alone.

    The decode is pinned to UTF-8 for the same reason git() pins its own, and against the same
    condition git()'s docstring now states rather than the LC_ALL=C this one used to name: a
    non-UTF-8 locale reached with PEP 538 coercion and PEP 540 UTF-8 mode both out of the way
    leaves the preferred encoding at US-ASCII. A manifest is JSON, which is UTF-8 by definition,
    so what the reader's locale prefers is not a fact about the file.

    Returns (None, reason) for every unusable state, so that the caller has one not-checked
    exit and one envelope shape rather than one per failure.
    """
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None, f"no manifest at {MANIFEST}: run `drift.py record` before `check`"
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, (f"the manifest at {MANIFEST} could not be read "
                      f"({type(e).__name__}: {e}), so nothing here has been compared against "
                      "the code; re-record")
    # Everything below reads `old.get(...)`. A manifest holding a list or a bare null parses
    # and then raises AttributeError at the first read, which is the same traceback at exit 1
    # one step later.
    if not isinstance(data, dict):
        return None, (f"the manifest at {MANIFEST} holds a JSON {type(data).__name__} where an "
                      "object was recorded, so there is nothing here to compare against; "
                      "re-record")
    # The same class one field deeper: the object parses, and then a field of the wrong type
    # raises at the point it is used rather than at the point it is read.
    wrong_shape = manifest_shape_error(data)
    if wrong_shape:
        return None, (f"the manifest at {MANIFEST} {wrong_shape}, so nothing here has been "
                      "compared against the code; re-record")
    return data, ""


def resolves(anchor):
    """Whether the anchor still names a line that is there, and why not when it does not.

    Only the end of the file used to be tested, so `src/cli/main.py:0` came back resolvable —
    while cited_line() enumerates from 1 and can never return a line 0. The anchor was
    resolvable and unhashable at once: record wrote no digest for it, check counted it as an
    anchor nothing had measured, and the set reported hashes_partial at exit 2 with a note
    saying re-record. Re-recording produced the same manifest and the same exit 2, so the state
    was permanent and no edit to the citation could clear it.

    Line numbers are 1-based everywhere an anchor is written, so 0 names no line and the
    citation is broken in the way a citation into a deleted file is broken. Reporting it here
    names the page to fix, and it makes the invariant the line half leans on true: a line that
    cannot be hashed is a line that does not resolve.
    """
    target, _, lineno = anchor.rpartition(":")
    if int(lineno) < 1:
        return False, "line numbers start at 1"
    path = os.path.join(ROOT, target)
    if not os.path.isfile(path):
        return False, "file does not exist"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            n = sum(1 for _ in f)
    except OSError:
        return False, "unreadable"
    if int(lineno) > n:
        return False, f"file has only {n} lines"
    return True, ""


def cited_line(anchor):
    """The text of the line an anchor points at, or None when it cannot be read.

    Decoded with surrogateescape rather than replace, because this line is about to be hashed.
    `replace` maps every byte it cannot decode to the same U+FFFD, so two different bytes on a
    cited line produced one digest: a latin-1 file whose cited line changed still matched its
    recorded hash and the check reported clean — under a user story titled "A cited file edit
    is never reported clean". surrogateescape gives each undecodable byte its own code point
    and re-encodes to the byte it came from, so the digest is taken over what is in the file.

    A file that is valid UTF-8 decodes identically either way and carries no surrogates to
    encode, so every digest recorded before this change still matches. This is also the decode
    git() uses, for the same reason: a byte is not a reason to stop answering.
    """
    target, _, lineno = anchor.rpartition(":")
    path = os.path.join(ROOT, target)
    try:
        with open(path, encoding="utf-8", errors="surrogateescape") as f:
            for i, line in enumerate(f, 1):
                if i == int(lineno):
                    return line
    except (OSError, ValueError):
        return None
    return None


def line_hash(anchor):
    """16 hex characters of sha256 over the cited line, surrounding whitespace stripped.

    Stripped, because reindenting a block moves no claim: the sentence the page makes about
    that line is still true, and reporting drift for it would train the reader to re-record
    without reading. Truncated to 16, because this is a change detector and not a signature,
    and a manifest is a file people open. Returns None when the line is not there at all, which
    is a broken anchor and is reported under that name with its own reason.
    """
    line = cited_line(anchor)
    if line is None:
        return None
    return hashlib.sha256(line.strip().encode("utf-8", "surrogateescape")).hexdigest()[:16]


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if not os.path.isdir(DOCS):
        print(json.dumps({"status": "no_docs", "docs_dir": DOCS})); return 2

    paths = doc_files()
    anchors = collect_anchors(paths)
    head = head_sha()

    if mode == "record":
        # No makedirs here: main() returns no_docs at exit 2 above for anything that is not
        # already a directory, so record cannot be reached with a DOCS to create. The call that
        # used to sit on this line could never make one, and reading as though it could is how
        # a guard gets moved later by someone who believes this branch still covers it.
        #
        # An anchor whose line number is past the end of its file gets no entry: it is already
        # a broken anchor at check time, and inventing a hash for a line that does not exist
        # would report the right failure under the wrong name.
        line_hashes = {}
        for a in sorted(anchors):
            h = line_hash(a)
            if h is not None:
                line_hashes[a] = h
        # Pinned to match read_manifest(): the reader decodes UTF-8, so the writer must not
        # be left to the locale's preferred encoding on the machine that happens to record.
        with open(MANIFEST, "w", encoding="utf-8") as f:
            json.dump({"gitHead": head or None, "docsHash": tree_hash(paths),
                       "pages": [os.path.relpath(p, ROOT) for p in paths],
                       "anchors": anchors, "lineHashes": line_hashes}, f, indent=2)
        # A recorded HEAD of "" was indistinguishable from a HEAD that still matched, so check
        # skipped the diff and called it clean. null plus a note says which one it is.
        print(json.dumps({"status": "recorded", "gitHead": head or None,
                          "pages": len(paths), "anchors": len(anchors),
                          "lineHashes": len(line_hashes),
                          "note": "" if head else NO_HEAD_NOTE}))
        return 0

    # check
    broken = []
    for a, cited_by in sorted(anchors.items()):
        ok, why = resolves(a)
        if not ok:
            broken.append({"anchor": a, "reason": why, "cited_by": cited_by})

    old, manifest_note = read_manifest()
    if old is None:
        # broken_anchors, not `broken`. This was the one status that published the list under a
        # second name, while the module docstring above and audit.md both name broken_anchors
        # as THE field a reader looks in — so a consumer reading it got nothing back from a
        # no_manifest run, which is the run most likely to be holding broken anchors, nothing
        # here having been recorded yet.
        print(json.dumps({"status": "no_manifest", "docs_dir": DOCS,
                          "anchors": len(anchors), "broken_anchors": broken,
                          "note": manifest_note}, indent=2))
        return 2

    recorded = old.get("gitHead")
    # ROOT-relative, like every anchor, page and broken-anchor path in the envelope below.
    # under_root() is what puts git's repository-relative answers into this namespace first.
    # os.curdir is what relpath returns when the docs directory is ROOT itself; code_paths()
    # reads the empty prefix as "test membership against the page set instead".
    docs_rel = os.path.relpath(DOCS, ROOT)
    inside_docs = "" if docs_rel == os.curdir else docs_rel + "/"
    docs_paths = {os.path.relpath(p, ROOT) for p in paths}
    docs_paths.add(os.path.relpath(MANIFEST, ROOT))

    # The code half. A git question that cannot be asked becomes a status of its own rather
    # than an empty answer; not_checked holds the one that stops the run at exit 2.
    #
    # Every note below names what WAS measured as well as what was not. They used to say the
    # pages had been compared "against nothing", and that was false in all three branches: the
    # anchors half has already run by the time this line is reached (ST-004) and is printed
    # under every status but no_docs — which returns above doc_files(), having found no
    # directory to walk, and carries no anchor field of any kind — and in the
    # two branches that reach the porcelain read the working tree is measured too — suspect
    # pages found that way are listed in the same envelope the note appears in. A not-checked
    # note that understates its own run teaches the reader to skip the findings beside it,
    # which is the same false pass as a clean that was never earned, arriving by the other road.
    #
    # What they may claim stops at those two halves. Each branch also asserted that "the
    # recorded line digests were still compared", and from here none of them can know: the line
    # half runs below, and against a manifest written before lineHashes existed (AC-006) it
    # compares nothing at all — so a pre-change manifest checked without a repository printed
    # no_git beside a sentence saying the line half had covered what the code half could not.
    # The only clause that ever corrected it fired for `partial`. That sentence is written with
    # the rest of the notes instead, at the first point where `hashes` can say which of the
    # three things happened. Overstating a run is the same failure as understating one.
    changed, dirty, not_checked, git_note = [], [], "", ""
    # The paths git named, in git's own namespace and before either narrowing below. This is
    # the suspect oracle, and it has to hold every path git printed: under_root() and
    # code_paths() each drop a population for the code-churn COUNT, and the oracle used to be
    # read off what they returned, so a cited file living outside ROOT (a `../` citation across
    # a monorepo package boundary) or inside the docs tree (a code sample beside the page that
    # explains it) had its only git signal filtered away, and a committed edit to a non-cited
    # line of it printed clean at exit 0 under a user story titled "A cited file edit is never
    # reported clean". The counts stay narrow; the oracle stops being their by-product.
    git_paths = set()
    # ROOT is the repository root until git says otherwise, which is what repo_prefix() below
    # asks. Every branch that never asks leaves git_paths empty, so nothing is mapped against it.
    repo_root = ROOT
    if head is None:
        not_checked = "no_git"
        git_note = ("git could not resolve HEAD (not installed, not a repository, or no commits "
                    "yet), so the anchors were resolved but neither the working tree nor the "
                    "diff since the record was read: suspect_pages is empty here because "
                    "nothing looked, not because nothing moved")
    else:
        try:
            prefix = repo_prefix()
            repo_root = repo_root_of(prefix)
            named = porcelain_paths(
                git("status", "--porcelain", "-z", "--untracked-files=all"))
            git_paths.update(named)
            dirty = code_paths(under_root(named, prefix), inside_docs, docs_paths)
            # A manifest with no recorded commit has no point for the diff to start from, and
            # falling past this to compare nothing is the false clean NO_HEAD_NOTE promises the
            # reader of that record will not happen. git answering now does not make a record
            # taken when it could not answer into a measurement.
            if not recorded:
                not_checked = "no_git"
                git_note = ("this manifest records no commit — record ran where git could not "
                            "resolve HEAD — so the working tree was read and the anchors "
                            "resolved, but the diff since the record could not be taken, having "
                            "no commit to start from; re-record")
            elif not commit_exists(recorded):
                not_checked = "head_missing"
                git_note = (f"the recorded commit {recorded[:12]} is not in this repository any "
                            "more, so the working tree was read and the anchors resolved, but "
                            "the diff since the record could not be taken; re-record")
            elif recorded != head:
                # --no-relative: with diff.relative set true in a user's ~/.gitconfig this diff
                # names its paths from the directory it ran in rather than from the repository
                # root, and repo_prefix() records what that cost. The porcelain read above
                # needs no such flag, having no such setting.
                named = git("diff", "--name-only", "--no-relative",
                            f"{recorded}..HEAD").splitlines()
                git_paths.update(path for path in named if path)
                changed = code_paths(under_root(named, prefix), inside_docs, docs_paths)
        except GitUnavailable as e:
            # git answered the first question and not the second. Reporting the half-answer as
            # a result is the false pass this whole file exists to stop.
            not_checked, changed, dirty, git_paths = "no_git", [], [], set()
            git_note = (f"git stopped answering part-way through the check ({e}), so the "
                        "working-tree reading was discarded along with the diff; the anchors "
                        "were still resolved")

    # The line half. A cited file can be touched without the cited line moving, and a cited
    # line can change under a file name that a diff since the record never names, so the two
    # halves of this check catch different failures and neither one subsumes the other.
    recorded_hashes = old.get("lineHashes")
    # The anchors the RECORD held. An anchor the current tree has and the record does not is a
    # citation the author added afterwards, and there was nothing to take a digest of when
    # record ran — so counting it as an anchor nothing measured made an ordinary docs edit,
    # one sentence carrying a new citation, exit 2 under hashes_partial with a note saying
    # re-record, where FR-008 reads unrelated_changes at exit 0. docs_edited_since_record is
    # the field that reports a docs edit, and it already does.
    recorded_anchors = old.get("anchors")
    # A manifest naming no anchors at all cannot tell the two cases apart, and every other
    # branch in this file takes the stricter reading when it cannot say. record has always
    # written the key, so this is a hand-edited manifest rather than an old one.
    recorded_set = set(recorded_anchors) if isinstance(recorded_anchors, dict) else None
    # An anchor whose line is past the end of its file is already in broken_anchors with the
    # reason it is gone, and FR-034 says record writes no digest for one. It is unresolvable
    # rather than unhashed, and counting it here would report one failure as two.
    broken_set = {b["anchor"] for b in broken}
    resolvable = [a for a in sorted(anchors) if a not in broken_set]
    mismatched, unhashed, added = [], [], []
    if recorded_hashes is None:
        # FR-004 / AC-006: a manifest written before lineHashes existed. No comparison is
        # possible, none is invented, and the absence is never itself a reason to report drift.
        hashes = "not_recorded"
    else:
        for a in resolvable:
            want = recorded_hashes.get(a)
            if not want:
                # An anchor the record itself held, resolvable now and carrying no digest, was
                # never compared against anything and still cannot report a pass.
                if recorded_set is None or a in recorded_set:
                    unhashed.append(a)
                else:
                    # A citation the pages grew after the record. Nothing was skipped here —
                    # there was no line to take a digest of when record ran — but it is counted
                    # rather than dropped, because the notes below carry numbers and a note that
                    # cannot see this population states them over the wrong one: the anchor sits
                    # in `resolvable` while nothing compared it, so a count of unhashed anchors
                    # "of len(resolvable)" understated the gap, and a sentence quantified over
                    # every resolvable anchor claimed a comparison this loop had not made.
                    added.append(a)
                continue
            now = line_hash(a)
            if now is not None and now != want:
                mismatched.append(a)
        # `checked` used to be printed whenever lineHashes was a dict, however few of the
        # anchors it held a digest for, and an anchor with no digest was skipped in silence: a
        # manifest that hashed one of two anchors was labelled checked and reported clean at
        # exit 0 while the other cited line had been rewritten. An anchor that resolves but was
        # never hashed is not-measured, and the two readings on offer were exit 0 with a note
        # and the exit-2 not-checked shape the Technical Design makes the template for every
        # new not-checked status. This takes the stricter one, for the reason no_anchors is
        # already exit 2: a gate that cannot say whether a claim still holds must not answer
        # with the code that means it does. `checked` therefore means every anchor this set
        # cites now, that resolves now, and that the record holds a digest for was compared.
        # Three populations sit outside that, and the field says nothing about any of them: a
        # citation the pages grew after the record, there having been no line to take a digest
        # of when record ran; an anchor the record held whose file or line is now gone, which
        # `resolvable` above excludes and broken_anchors reports by name; and an anchor the
        # record held whose citation the pages have since DROPPED, which is not in this tree's
        # anchors at all and so is never reached by the loop above. The last two can empty the
        # loop's set between them, so a run that compared none of the record's digests still
        # prints `checked`.
        hashes = "partial" if unhashed else "checked"

    # The loop split `resolvable` three ways: compared, unhashed, and the citations the pages
    # grew after the record. The first two are the anchors the record held and this run had
    # something to say about, so they are the population the digest notes below count over; the
    # third is counted separately, under its own name, and never folded into a total with them.
    recorded_resolvable = len(resolvable) - len(added)

    # A page is suspect when a file it cites is one of the paths git named. The anchor is put
    # into git's namespace rather than the paths into the anchors', because the page chooses
    # how it spells a citation — `../shared/lib.py` and an absolute path can name one file —
    # and normpath resolves both of those spellings to the path git prints, where a string
    # comparison would give each of them its own answer.
    suspect = {}
    for a, cited_by in anchors.items():
        if in_repo_space(a.rpartition(":")[0], repo_root) in git_paths:
            for page in cited_by:
                suspect.setdefault(page.split(":")[0], []).append(a)

    unanchored = [os.path.relpath(p, ROOT) for p in paths
                  if os.path.relpath(p, ROOT) not in {c.split(":")[0]
                                                      for v in anchors.values() for c in v}]
    docs_edited = tree_hash(paths) != old.get("docsHash")
    # Committed since the record and uncommitted right now are both "the code moved". A file
    # in both sets is one changed file rather than two.
    code_files_changed = len(set(changed) | set(dirty))
    # A set with no anchors resolves every anchor it has, which is not the same as being
    # checked. Reporting that as clean is a false pass, and a false pass is worse than a
    # finding because the reader trusts the page exactly as far as they trust the gate.
    # `and paths` required the page list to be NON-empty, so the one set that had been looked
    # at least of all — a docs tree holding no page — was the one that fell through to clean at
    # exit 0. Zero anchors is zero anchors however few pages produced them.
    nothing_to_measure = not anchors

    # Precedence, least measurable first. A set that cannot be measured at all, then a git
    # question that could not be asked, then the findings, then the changes that are nobody's
    # problem. The first two exit-2 statuses outrank drift because "I could not check anything"
    # is a different claim from "I checked and it is wrong" — and the anchors half is printed
    # under both. hashes_partial sits BELOW drift on purpose, because it is the narrower claim:
    # some anchors were measured and some were not, so a finding the run did make is still
    # reported as a finding (FR-007) and "I could not check this one" never hides "I checked
    # that one and it is wrong".
    if nothing_to_measure:
        status = "no_anchors"
    elif not_checked:
        status = not_checked
    elif broken or suspect or mismatched:
        status = "drift"
    elif unhashed:
        status = "hashes_partial"
    elif code_files_changed or docs_edited:
        status = "unrelated_changes"
    else:
        status = "clean"

    notes = []
    if nothing_to_measure:
        notes.append("this docs directory holds no page at all, so nothing here was scanned "
                     "and the Sourced gate cannot report a pass" if not paths else
                     "no page in this set cites a source, so nothing here can be re-verified "
                     "and the Sourced gate cannot report a pass")
    if git_note:
        notes.append(git_note)
    # The line half's own sentence, and the first point in this function where `hashes` exists
    # to make it conditional — which is why the git branches above no longer try to say it. The
    # two arms below `partial` are stated only under a not-checked status, where the reader is
    # being told a half of the run did not happen and needs to know whether this one did; on a
    # run that answers, the `hashes` field says the same thing without a sentence, and the note
    # stays what it was.
    if unhashed:
        # Numerator and denominator over one population. The count of anchors the record held
        # no digest for was printed "of len(resolvable)", and len(resolvable) also holds the
        # citations the pages grew afterwards — which the numerator cannot contain, there being
        # no record entry to be missing a digest from. A manifest missing one digest of the two
        # it held, in a tree that had since grown a third citation, read as "1 of 3 resolvable
        # anchors" where 1 of 2 is what was measured: a gap understated by whatever the pages
        # added in between, in the one note whose whole job is to state its size.
        grown = ((f", and {len(added)} further anchor(s) resolve here that the record does not "
                  "hold at all — the pages grew those citations after it, so there was no line "
                  "to take a digest of when record ran") if added else "")
        notes.append(f"no digest was recorded for {len(unhashed)} of the {recorded_resolvable} "
                     "anchors that resolve here and this record should hold a digest for, so "
                     "the lines listed under unhashed_anchors were never compared against what "
                     f"they said at record{grown}; re-record")
    elif not_checked and hashes == "not_recorded":
        notes.append("this manifest holds no line digests at all, so no cited line was compared "
                     "against what it said at record either; re-record writes the map")
    elif not_checked and resolvable:
        # Quantified over the anchors the comparison loop actually reached, which is not every
        # anchor that resolves. This sentence used to say "every resolvable anchor carried a
        # recorded digest and was compared", and a citation added after the record is resolvable
        # and was never compared — the loop passes over it without a digest and without counting
        # it — so on a tree that had grown one, a not-checked run told the reader the line half
        # had covered a set wider than the one it read. A broken anchor the record DID hold a
        # digest for is outside this sentence too, for the same reason: it is in broken_anchors,
        # not in anything that was compared.
        #
        # A third population is outside it, and it is the one the wording used to claim
        # outright: an anchor the record holds a digest for whose citation the pages have since
        # DROPPED. It still resolves and the record still holds its digest, and this loop never
        # saw it, because the loop iterates the anchors THIS tree cites. "every anchor that
        # resolves here and that the record held a digest for" named that anchor and said it
        # had been compared, on a run where nothing had looked at it. The sentence starts from
        # the set the loop reads instead.
        grown = ""
        if added:
            # docs_edited_since_record is where a citation the pages grew is reported, and it is
            # named only where this run actually observed it: an anchors map hand-edited out of
            # the manifest reaches this same count with the docs tree untouched, and pointing at
            # a false field would be the same overstatement one sentence later.
            grown = (f", and {len(added)} citation(s) here are not in the record at all, so "
                     "there was no digest to compare them against"
                     + (" — the docs edit docs_edited_since_record reports" if docs_edited
                        else ""))
        notes.append("every anchor this set cites now that resolves here and that the record "
                     "holds a digest for was compared against the line as it stands now"
                     f"{grown}")
    if status == "unrelated_changes":
        notes.append(f"{code_files_changed} code file(s) changed since the record and no page "
                     "cites any of them" if code_files_changed else
                     "pages were edited since the record and no code a page cites changed")
    if unanchored:
        notes.append(f"{len(unanchored)} of {len(paths)} pages cite no source")

    print(json.dumps({
        "status": status,
        "gitHead": {"recorded": recorded, "current": head},
        "docs_edited_since_record": docs_edited,
        "code_files_changed": code_files_changed,
        "anchors": len(anchors),
        "pages": len(paths),
        "pages_with_no_anchor": len(unanchored),
        "unanchored_sample": sorted(unanchored)[:12],
        "broken_anchors": broken,
        "suspect_pages": suspect,
        "hashes": hashes,
        "hash_mismatches": mismatched,
        "unhashed_anchors": unhashed,
        "note": "; ".join(notes),
    }, indent=2))
    if status in ("no_anchors", "no_git", "head_missing", "hashes_partial"):
        return 2
    return 1 if status == "drift" else 0


if __name__ == "__main__":
    sys.exit(main())
