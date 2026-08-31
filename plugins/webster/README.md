# webster

**Documentation for the person who has to use the thing, where every claim carries its source
and the ones that could not be checked say so.**

Four documentation plugins were installed to do this job. Two of them are the same plugin: the
`docs-architect` and `tutorial-engineer` agents in `code-documentation` are byte-identical to the
ones in `documentation-generation` apart from the `name:` line, and `/doc-generate` is identical
in both. Between them they could write a great deal of documentation. None of them could tell you
which sentences were true.

webster merges them into one surface and adds the missing half.

## What was merged

| Source | What was taken |
| --- | --- |
| `documentation-generation` | All five agents, and the ADR, changelog and OpenAPI skills unchanged |
| `code-documentation` | Nothing. It is a strict subset of the above |
| `documentation-standards` | The HADS tags: `[SPEC]`, `[NOTE]`, `[BUG]`, `[?]` |
| `repo-doctor` | The nine-criterion README rubric, scored with cited evidence |
| A UI slop-rule corpus | Copy and residue rules, retargeted from rendered HTML to markdown and diagrams |
| The Good Docs Project | The content types, their sections, and what each type may not contain |
| ISO/IEC/IEEE 26514 | The nine quality characteristics. Four are measured here, one by `drift.py`, two in part, and two a person judges |

Dropped on purpose: the generic `/doc-generate` command, which is a large stack-agnostic template
dump written around Python, FastAPI and Sphinx, and produces noise in a repo that is none of those.
`/webster:plan` surveys the actual repo instead.

## Install

```
/plugin marketplace add <the checkout holding .claude-plugin/marketplace.json>
/plugin install webster@guild
```

The marketplace this plugin ships in is named `guild` and lists webster at `./plugins/webster`.
These two lines used to read `add ~/Projects/webster` and `install webster@webster`, which named
a directory that is not a marketplace and a marketplace that does not exist, so neither command
could have run.

## Use

```
/webster:plan     see the product the way a person does, name the readers and the subjects,
                  scaffold the tree and the site
/webster:write    extract reference material, write the pages, record the manifest
/webster:audit    drift, layout, seven gates, prioritised punch list
```

## Every page declares who it is for

This is the rule the rest of the plugin hangs off, and it is the one that decides whether the
documentation is about the product or about the code.

```yaml
doc_type: how-to
audience: operator
```

`audience` decides **what the page may be about** first, and how hard its sentences may be
second. That order matters, because a page can describe a status enum in short words and still be
useless:

> A deployment's status is the field everything else in the UI hangs off. This page is about how
> that field moves.

Nothing there is untrue, nothing is long-winded, and it is a developer's model of the system
handed to somebody who wanted to know whether their cluster was ready.

| Audience | May name | May not name |
| --- | --- | --- |
| `user` | Screens, buttons, fields, menus, their own files, what the product gives back, what it says when something fails | Internal symbols, any route path, command-line flags, environment variables and architecture |
| `operator` | The above, plus commands, flags, config files, variables, ports, logs | Symbols, architecture |
| `developer` | Anything in the system | nothing |

Those pages all exist. `developer/` and `advanced/` are where the internals go, and they declare
a different reader. Moving a page is the fix; editing `audience:` until the checker goes quiet is
not. `WEBSTER_LENS_ALLOW` widens the vocabulary where a product's readers genuinely use it, and
a Kubernetes tool's operators really do handle controllers. `WEBSTER_SURVEY` widens it without a
list to maintain: point it at a saved `survey.py` JSON and every screen name, button label and
subcommand the product already prints stops being read as an internal symbol. Absent or
unreadable it allows nothing extra and the check says so in its header line, because an
allowlist that quietly got smaller looks exactly like one that worked.

A page that declares no reader is a defect rather than a note, which is a correction rather than
a preference: in the last full run before the rule existed, none of 67 pages declared an
audience, so every rule that depended on one was inert.

## The product is read before the source is

`/webster:plan` looks at what a person can see and do before it opens a single handler.
`survey.py` returns both surfaces:

- **`user_surface`**, the screens, the words printed on the buttons and headings, the error
  messages, the subcommands. This is what the subject list and the vocabulary come from.
- **`surface`**, routes, exports, environment variables. This is how a sentence gets checked,
  not a table of contents.

The order is the point. Documentation written after a morning in the source describes the source,
because that is what was read, and no amount of editing afterwards undoes it.

## The scripts

The mechanical half is seven scripts rather than prompts, so it is deterministic and runs in CI
without an agent. They are standard library only, and the floor is Python 3.11: `survey.py` and
`llmstxt.py` parse `pyproject.toml` with `tomllib`, which is standard library from 3.11 and has
no fallback path here.

| Script | What it does |
| --- | --- |
| `scripts/survey.py` | Two surfaces, both anchored to `file:line`. **`user_surface`**: the screens, the text on the buttons, headings and fields, the product's own error strings, the subcommands somebody types. **`surface`**: HTTP routes from Next.js App Router and Pages API, Express, Fastify, Hono, FastAPI, Flask and Go mux, plus CLI binaries, package exports, env vars and OpenAPI specs. Names the extractors that fit the stack |
| `scripts/drift.py` | `record` stores HEAD, a hash of the docs tree, every anchor the pages cite and a digest of each cited line. `check` reports anchors that no longer resolve, cited lines whose digest moved, and pages whose cited code changed. `clean` and `unrelated_changes` exit 0; `drift` exits 1; `no_docs`, `no_manifest`, `no_anchors`, `no_git`, `head_missing` and `hashes_partial` exit 2. `clean` is reserved for a set where nothing changed at all, so code that changed under no citation is `unrelated_changes` rather than a finding; a set that cites no sources is `no_anchors` rather than `clean`, and an anchor the record held but never took a digest of leaves the run `hashes_partial`, because resolving every anchor you have is not the same as having been checked |
| `scripts/scaffold.py` | Writes the documentation tree in the layout Harvester uses, subject-first directories with `_category_.json` ordering, plus a Docusaurus site whose sidebar is generated from the filesystem, and validates an existing one. A run that reaches a mode prints a JSON object whose first key is the status, the run that went right included, so a caller reading that key never has to guard the one path where nothing went wrong; an argv that reaches neither — no mode at all, or a word that is not init or check — stops in argparse, which writes usage to stderr and leaves stdout empty, so on that one path there is no key to read. Writing a tree reports `ok` at exit 0, and `bad_subject` for a subject key that cannot become a directory name or `cannot_write` for a path the filesystem refused exit 2; init never exits 1. Validating one reports `violations` at exit 1, `ok` at exit 0, and `no_docs` for a missing directory or `cannot_read` for a page or a directory the filesystem refused exit 2 |
| `scripts/doctype.py` | Per-page content type **and reader**. Skeletons from The Good Docs Project, quality checks from the ISO/IEC/IEEE 26514 characteristics. Defects (no declared reader, subject matter that reader cannot act on, an acronym the docs never expand, a user explanation page that never addresses the reader, type mixing, missing alt text, skipped heading levels) exit 1, advisories alone (no prerequisites, no table, reading grade, vocabulary pointing at the machinery) exit 0, and a tree with nothing to check — no docs directory, or no page that is not a stub and no frontmatter defect on the stubs either — exits 2. Findings are grouped by rule with a count |
| `scripts/slop.py` | A copy and residue rule corpus retargeted to markdown, plus tells specific to docs and to generated diagrams. Prose rules skip code fences, diagram rules run only inside `mermaid`, `d2` and `dot` fences. Exit 1 on any high severity finding, exit 2 on a target that is not there or cannot be read |
| `agents/webster-reader` | Not a script. One agent per page, given the page and its declared audience and nothing else, reporting where the page stopped it. The half of Readable no checker can reach, and the one thing in this plugin whose value is entirely in what it has not read |
| `scripts/prose.py` | Measures the shape of the prose: sentence length, paragraph length, words under a heading, and the passive and nominalised constructions that hide the actor. Thresholds scale with the page's declared audience, and `limits` prints them. Answers a different question from the Flesch-Kincaid ceiling in `doctype.py`, which is a statistic over syllables and rates monosyllabic passive prose at grade -0.2 while charging a clear sentence 10.2 for naming the product. A `long-sentence` or `dense-section` exits 1; advisories alone (`long-paragraph`, `passive-voice`, `nominalisation`, `fragmented`) exit 0; no docs directory at the resolved path, or a tree of nothing but stubs, exits 2 |
| `scripts/rendered.py` | Reads the built HTML rather than the markdown, and reports anything internal that reached the reader: a visible `file:line`, a source path, a working-note tag, a frontmatter key printed as text, an agent instruction file named by name, and a stub marker on a page nobody wrote over. The only check that sees what a browser sees. Exit 1 on any leak |
| `scripts/llmstxt.py` | Builds an llms.txt to the llmstxt.org format from pages that exist on disk. The written file is exit 0; no docs directory at the resolved path, or a tree holding no page to publish — a tree of nothing but stubs included, because an unwritten page is dropped before the count is taken — exits 2 |

## The layout is fixed

Every set of documentation this plugin produces uses the same tree, modelled on
[Harvester](https://github.com/harvester/docs): subject-first directories at the top level, a
Docusaurus site whose navigation is the filesystem, and Diataxis applied one level down to decide
how a page is written rather than where it lives.

```
docs/index.md  faq.md  getting-started/  install/  <subject>/...  api/  advanced/
troubleshooting/  developer/
```

A reader who learns where things live in one product finds them in the same place in the next.
A small product still gets the full tree, because an empty section is an honest visible gap and
the shape can be grown into.

Reference pages are extracted, not composed. A model writing an API reference from a code read
produces a table that is correct on the day and wrong by the next commit. See `skills/extraction`.

## The seven gates

A page is not finished until all seven pass, and a gate that could not be run reports
`not_checked` with a reason rather than a pass.

| Gate | Passes when |
| --- | --- |
| Sourced | Every behavioural claim has a resolving anchor, held in a comment or in frontmatter rather than shown to the reader |
| Runnable | Every non-illustrative example has been executed |
| Shaped | The tree passes `scaffold.py check` and no page is doing another type's job |
| Lens | Every page declares a reader, and names nothing that reader cannot touch |
| Builds | The site compiles, reports no broken links, and nothing internal reached the rendered pages |
| Readable | The mechanical checks are clean, and somebody who did not already know the answer got through it |
| Honest | "What this is" and "what this deliberately does not do" both exist |

The table carried an eighth row, `Unslopped`, under a heading that says seven and a sentence
that says seven, and `skills/house-rules` — the list `/webster:audit` actually runs — has never
held it. A high severity slop finding is a P1 on the punch list rather than a gate, and the
`slop.py` row above is where this README describes it.

**Readable used to be one gate and is now two halves, both of them webster's own.** It was
delegated whole to `courseware:learner`, which most repos do not have, so it reported
`not_checked` every time and the only gate that catches a page a person cannot follow never ran.

The mechanical half is `prose.py` and the readable rules in `doctype.py`: sentence and section
length against the audience, unexpanded acronyms, a stated outcome, prerequisites before step
one.

The other half is `webster-reader`, one agent per page, handed the page and its declared
audience and nothing else. It reports where the page stopped it, in a closed vocabulary:
`stopped`, `assumed`, `unresolvable`, `reread`, `orphan`. Its value is entirely in what it does
not know, so it is never briefed on the product, and whoever wrote the page cannot stand in for
it: with the source open, every gap on the page gets filled from the writer's own head before
they notice it is a gap. When only the mechanical half ran, or only some pages were read, the
verdict is `partial`, which is not a pass.

## Optional companions

webster works alone. Where one of these is installed, the step that names it reaches for it
instead of reimplementing it. Only the first is attached to a gate, and its absence leaves that
gate `partial` rather than `not_checked`, because the mechanical half of Readable still ran and
a verdict on half a gate is not the same as a verdict on none of it. The other two save work
inside a step rather than settling anything, so nothing is downgraded when they are missing:

- `courseware:learner`: a second naive-reader pass, where a repo already has it. The Readable
  gate no longer depends on it: `prose.py` is the mechanical half and `webster-reader` is the
  other, so the verdict without it is `partial`
- `gsd:map-codebase`: the repo survey in `/webster:plan`, where it saves re-deriving a map by
  hand and settles no sentence
- `repo-visuals`: hero images for a published docs site, which the README rubric declines to
  score either way

The Sourced gate has no companion. This list gave it `plumb` or `hollis:checker`, and neither
name appears anywhere else in the plugin: anchor resolution is `drift.py check`'s own, and a set
that cites nothing is `no_anchors` at exit 2 rather than a gate waiting on somebody else's tool.
The sentence above this list used to promise `not_checked` for all four, which was true of none
of them.

## Status

Version 0.13.0.

```
cd plugins/webster && uvx pytest
```

163 passing across nine modules. Every module but `tests/test_readme.py` drives a script the
way a command does, through the one `run_script` helper as a subprocess, so nothing is imported
and the file under test is the one a user actually invokes. Five of the eight scripts make an
import the wrong way in: `drift.py`, `llmstxt.py`, `doctype.py`, `survey.py` and `slop.py` bind
their root, their docs directory or their allowlists at module import, out of argv or the
environment, so an imported copy stays frozen at the importing process's values — measured, an
imported `drift.py` went on naming the directory the import had run in after the process had
moved off it. Frozen is not sealed, and this paragraph used to overstate it as "no other way
in": `runpy.run_path` re-executes a module and re-binds all five, and drove `drift.py check` to a
real JSON line and exit 2 inside this process. The other two read their arguments inside `main`
— `scaffold.py` through `argparse`, `rendered.py` through `sys.argv` — and an import with
`sys.argv` set is enough for either; measured on `scaffold.py`, that returns its `bad_subject`
envelope and exit 2 with no subprocess at all. What every in-process shape skips is the process
boundary where the environment `run_script` builds and the interpreter it resolves apply at all,
which is why one invocation shape drives every script the suite drives, `scaffold.py` included.
`rendered.py` it drives nowhere, as the paragraph below on what is not yet exercised says. The
split is written down because an earlier sentence here said "each script binds", named no script
and no number, and was therefore read as covering all seven. It was
false for `scaffold.py` and for `rendered.py`, and `scaffold.py` has a test module sitting one
directory away from the file that said it. `drift.py`'s cases all run against the committed
fixture repo in `tests/fixtures/repo/`, rebuilt into a real git repository with fixed commit
dates for each test.
The other modules build the one small tree a case needs under pytest's `tmp_path` and reach for
that fixture only where the case wants a real repository or a real page beneath it.

`run_script` calls the literal `python3` and passes PATH straight through, because that is what a
reader gets: every script carries a `#!/usr/bin/env python3` shebang and Claude Code runs it as
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/X.py`. Which interpreter that resolves to depends on how
the suite was launched, and both launches have been measured, the collector's `sys.executable`
realpath and version against the child's. Under the command above, uv puts its ephemeral
environment's `bin` first on PATH, so the `python3` the child resolves is uv's own CPython and is
the interpreter collecting the tests: one realpath, 3.11.14 on both sides here. Launched as
`python3 -m pytest` instead, PATH is the shell's, and the `python3` the shell resolved in order
to start pytest is the one the child resolves as well: one realpath again, 3.14.6 on both sides
here — measured through a virtualenv built on that interpreter, because this machine is PEP
668-managed and PATH's own `python3` has no pytest for `-m` to find. Collector and child agree
under both, and the clause this replaces, that under the second launch the child was "a different
interpreter from pytest's", is what the measurement refutes. What varies between the launches is
which interpreter both of them are, which is a property of the machine and of the launch rather
than of the suite: it pins neither, and the thing worth asserting is the floor they clear.

That count is itself checked. `tests/test_readme.py` reads this section back, compares the number
to the tests the suite defines, and compares the version above to `plugin.json`, so a test added
without touching this line fails the run that added it. The number sat at a stale value through
several additions before anything read it, which is the same shape as a gate that passes because
it never looked.

The same module reads the `drift.py` row of the script table above against that script's own
docstring, and for the same reason. The row went on describing an exit vocabulary the script had
stopped using: it named `no_anchors` and "exit 1 on drift" while the script had grown
`unrelated_changes`, `no_git`, `head_missing` and `hashes_partial` underneath it, so the one
status a reader most needed — the one that says ordinary development is not a finding — appeared
nowhere in the README. The status names are taken out of `drift.py` at test time rather than
listed in the test, because a list written in the test is a third place the vocabulary lives and
three copies drift the way two did.

Naming a status is not the same as placing it. That check was satisfied by the row mentioning
each name somewhere in it, so a row that swapped its exit-0 and exit-2 groups outright — telling
a reader that a missing repository was a clean run and that ordinary development was a
not-checked one — passed every assertion in the module. Every row of the table above is now read
the way `drift.py`'s is: the exit codes it publishes have to be the set the script's own usage
text publishes, and each status it backticks has to sit under the code that usage text returns
it at. Three rows were narrower than the script they describe. `scaffold.py`, `doctype.py` and
`slop.py` each published their exit 1 and nothing else, so a reader handed a 2 by any of them —
a subject key that could not become a directory, a tree with nothing in it to check, a target
that was not there — found no entry for it. One parser reads both sides, so whatever it reads
loosely it reads loosely for the script and for the row alike, and the two still have to agree.

Placing a status only reaches the rows whose script prints one, and only `drift.py` and
`scaffold.py` do. The other five compared nothing at all, and the tally meant to notice was one
counter across the whole table that those two kept above zero, so the `doctype.py` and `slop.py`
rows could have their exit groups inverted and the module stayed green — measured, not supposed.
The counter is per row now, and a row whose script publishes more than one code has to have had
something placed against it. Where there is no status, that something is the case under each
code, compared word for word with the usage text: the row's own filing has to fit at least as
well as any other pairing of the two. Inverting a row leaves the set of codes it publishes
identical, because a permutation of a set is that set, which is why the check above cannot see it
and this one does. The `doctype.py` row's own filing scores 23 shared words against that script's
usage text; swapping its exit-0 and exit-2 groups, which is the inversion this section has been
describing throughout, drops it to 4, swapping exits 1 and 2 drops it to 11, and swapping exits 0
and 1 drops it to 20. Every rearrangement scores below the row as it stands, which is the whole
of what the check asks of it. The figure recorded here was 11 with no swap named beside it, and
11 is the 1-and-2 number: the sentence had inherited an arithmetic result without the antecedent
that produced it, which is a measurement no reader can re-run. `rendered.py` and
`survey.py` are exempt rather than overlooked: one code and none, and a single code has no second
place to be filed under. The `llmstxt.py` row is the one that published no exit code at all, and
the missing half of that check was both sides going quiet together — two empty sets are equal, so
a row and a usage text that agree on saying nothing passed. That silence is now read against the
script's own `sys.exit` calls, and the exemption that follows rests on what the script publishes
about itself rather than on the absence alone. `survey.py` calls none, and its usage text states
the contract that turns that absence into an answer: a run which reads the repo prints the survey
and returns 0, whatever it found. A manifest it can read but cannot use is answered with that
file's contents missing, never with the survey missing — a `package.json` holding an array or
`null`, a `dependencies` field that is not a table, a `[project.scripts]` target TOML wrote as a
date. All three ended the run in a traceback at exit 1 until the guards that read them as absent
went in, and all three have a test. That is why it is the one script here with no contract to
publish: not that nobody wrote a `sys.exit`, but that it publishes an exit-0 answer for every
input it can read and holds it at the three places it used to break.

Exercised: six of the seven scripts, one test module each. `drift.py` across a dirty tree, a
staged rename, a missing repository, a rebased-away HEAD, a docs tree sitting at the repository
root, a developer's `diff.relative` setting that renamed the paths git printed, an anchor citing
line zero, a cited line that changed, a cited file sitting under the docs tree and another
sitting outside the docs root, both of which the change counts drop on purpose and the suspect
oracle must not, the `broken_anchors` key a `no_manifest` run has to publish its list under, and
the note a run with no commit prints about the digests it therefore did not compare;
`doctype.py` across the widened symbol, route and flag lenses, the acronym list, stubs, the
`WEBSTER_SURVEY` allowlist, the architecture rule that keeps firing for an operator after routes
and flags stopped, the pages a passing run's own closing line claims to have matched, and the
contract `types` prints to a writer before the lens reads them; `survey.py` across decorators
split over several lines, Flask's declared methods, `pyproject.toml`, `HTTPException` details,
`os.getenv` reads, the anchor every `surface` and `user_surface` entry carries where a `tooling`
entry has none to carry, the count its own red-first table states about itself, and the option
flags a parser declares — including a description that merely names another flag, which is not a
declaration, a declaration a formatter split across lines, which is, a decorator whose
parentheses never balance inside the lines the join reads, where no verb is published at all
rather than the `GET` a Flask default would otherwise have invented, and a parenthesis inside a
comment or a string, which is a character rather than syntax and used to end that join two lines
early, and the three manifest shapes that parse and are still not what the readers below expect:
a `package.json` that is not an object, a `dependencies` field that is not a table, and a
`[project.scripts]` target that is not a string, each of which took the whole survey down at
exit 1 before it was read as absent; `llmstxt.py` across the fixes it carries plus the scaffold
brace that stops a README line from becoming a summary and the checkout's directory name, the one
header source published exactly as the filesystem spells it because nothing follows it in the
chain, and the non-object `package.json` its own header chain opens on, which used to end it the
same way; `slop.py` across
its own plus the directories the walk prunes, which are neither read nor counted; and
`scaffold.py` across those plus the docs path it is handed and cannot write into, a page it
cannot read, a malformed `_category_.json`, a docs directory nothing can stat, a directory below
the top level that nothing can list, which is `cannot_read` rather than a tree read and found
sound, the violations collected before a refused read, which are dropped rather than reported
out of a scan that stopped, a `--title` or a `--subject` label carrying a byte no page can hold,
which leaves nothing written behind it, and the exit set its help text publishes for both modes
with the `status` key every one of those envelopes leads with. Every fix has at least one test that
fails against the script as it stood before the fix; the rest are guards, there so a fix does not
take something else away with it.

The harness under those modules is exercised as well. `tests/test_harness.py` measures five
things nothing else in the suite checks: that the child `python3` clears the plugin's 3.11 floor,
that two builds of the fixture repository land on the same commit, that a further build lands on
that same commit under a host time zone fourteen hours and a date line away, that the committed
fixture carries no nested `.git`, no `website/` and no recorded manifest, and that this change
stayed inside the files it was allowed to write. The first of those measures the floor rather
than naming an interpreter, and that is the correction: `conftest.py` used to say the child
resolved through PATH to 3.14.6 and was not the one running pytest, and under the command printed
above both halves are false. The third is the one the second could not supply: two builds in one
process agree with or without the explicit `+00:00` on the fixture's commit dates, and the
divergence that offset averts is between machines in different zones, which nothing here had
varied.

Not yet exercised: `rendered.py`, which has no test module, so every claim about what it catches
in built HTML is still untested. Its row above is also the one place in this table where both
copies of a contract are narrower than the script: the row publishes exit 1 on a leak because
that is what the script's own usage line publishes, and the script also returns 0 when nothing
internal reached the reader and 2 when there is no built site to read. The check that reads the
table compares the row against the usage text, so two narrow copies agree and pass; widening the
row alone would make them disagree, and the usage line is where the fix belongs. Nor the three
commands end to end, which means what `/webster:plan`, `/webster:write` and `/webster:audit`
produce rests on the scripts underneath them rather than on a run.

The name has had no trademark or product search. Do that before anything goes on it.
