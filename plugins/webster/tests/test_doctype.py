"""Tests for plugins/webster/scripts/doctype.py — the lens, the allowlists, the stubs.

One red-to-green test per fix (US-010, AC-039). Measured against commit cfafe8e, the pre-change
baseline AC-039 names, as the sibling modules do: 20 of the 27 tests in the first index below
fail there and all 27 pass after. The measurement is direct — a detached worktree at cfafe8e
with this tests/ directory and pyproject.toml copied in over a scripts/ directory left as
cfafe8e wrote it, and ``uvx pytest tests/test_doctype.py`` run inside it, which reports 24
failed and 10 passed across the whole module. An earlier version of this paragraph measured at
2abe081 — this suite's own first commit, the earliest place these tests could be run at all —
and carried the numbers across on the grounds that 2abe081 added the harness and touched no
script, so ``git show 2abe081:plugins/webster/scripts/doctype.py`` hashes to cfafe8e's bytes.
Those numbers were right and the sentence naming 2abe081 "the last one before this change" was
not. The other seven are negative controls that must pass on both sides, and each is marked
(control) below — a control that went red would mean the fix broke something it was supposed to
leave alone. The red-first property is recorded here rather than enforced at runtime (FR-039):
a test that re-ran itself against an old checkout would need a git worktree per assertion.

Each test names the fix it pins:

- FR-010 CODE_IDENT gains snake_case and matches acronym-initial PascalCase (OT-011, OT-012)
    test_snake_case_symbol_is_wrong_lens
    test_acronym_initial_pascal_is_wrong_lens
    test_path_and_env_var_are_not_symbols  (control)
- FR-011 ROUTE_PATH matches any backticked path but not a file (OT-013)
    test_plain_route_is_wrong_lens
    test_path_with_a_file_extension_is_not_a_route  (control)
- FR-012 the new FLAG regex, suppressed by the allowlist (OT-014)
    test_flags_are_wrong_lens_on_a_user_page
    test_flag_named_in_lens_allow_is_not_reported  (control)
- FR-017 / GI-003 routes and flags fire only for `user`; symbols and architecture for both
  (OT-015, AC-014)
    test_operator_page_is_not_reported_for_a_route_or_a_flag
    test_operator_page_is_still_reported_for_a_symbol
    test_architecture_is_reported_for_an_operator_and_for_a_user  (control)
- FR-015 IDENT_ALLOW compares lowercased and carries real product names (OT-016)
    test_product_names_are_not_internal_symbols
    test_no_ident_allow_entry_is_all_caps
- FR-013 / CT-003 WEBSTER_SURVEY extends the allowlist at runtime (OT-017)
    test_survey_labels_extend_the_allowlist
    test_without_the_survey_the_same_label_is_reported  (control)
    test_survey_screen_names_and_commands_extend_the_allowlist
- FR-035 the check header line states what the survey contributed (OT-017)
    test_header_line_counts_the_terms_the_survey_contributed
    test_missing_survey_file_does_not_crash
    test_malformed_survey_file_does_not_crash
- FR-016 / FR-041 / FR-033 API and CLI are universal; WARNING is gone (OT-018)
    test_api_and_cli_are_not_undefined_jargon
    test_every_universal_acronym_is_reachable_by_the_acronym_regex
- FR-014 stubs get the frontmatter checks and nothing else (OT-019)
    test_stub_without_audience_is_reported
    test_stub_placeholder_prose_produces_no_other_finding  (control)
    test_stub_with_an_unknown_audience_or_type_is_reported
- FR-014 / FR-036 an all-stub tree is not checked (OT-020)
    test_tree_of_only_stubs_exits_two_with_nothing_to_check
    test_one_written_page_among_stubs_exits_by_that_page  (control)
- FR-014 / AC-020 / AC-021 the pass line is worded over the pages that were read as writing
    test_the_pass_line_names_the_written_pages_it_matched
- FR-040 / CT-004 a defect on a stub beats not-checked (OT-042)
    test_all_stub_tree_with_a_defect_exits_one

Six more tests are measured against a later commit instead, because each pins a defect one of
the widenings above introduced rather than one it fixed. Four are measured against 9859317:

- FR-010 / AC-011 the widened CODE_IDENT still excludes ALL-CAPS
    test_all_caps_with_a_digit_is_not_an_internal_symbol
- FR-010 / FR-015 / AC-011 a versioned standard name is not an internal symbol
    test_versioned_standard_names_are_not_internal_symbols
- FR-013 / CT-003 / AC-016 a screen's name reaches the lens and a screen's path does not
    test_a_screen_name_reaches_the_lens_and_a_screen_path_does_not
- FR-040 / CT-004 zero pages checked is never exit 0
    test_empty_docs_tree_is_not_checked

The first two of those four pass at cfafe8e as well — the narrow CODE_IDENT could match neither
`HTTP2` nor `IPv4` — which is what makes them regression guards rather than fix guards. The
other two are red on both sides.

The last two are measured against 19941ad, the commit before this fix. One holds three lines
that the two rounds of route widening broke in turn, each attribution read off ``git show <sha>
-- plugins/webster/scripts/doctype.py`` rather than remembered. a5484ba, round one, replaced the
narrow /api and /v<n> rule with a six-letter cap on the file-extension exclusion, so a path whose
extension ran past six — `/etc/app.properties`, `/docs/readme.markdown` — was reported as a
route, while `.conf` is four letters, fitted the cap, and never was. The same commit opened the
path body on a slash, so `//` became a route with no segment in it. eb27489, round two, lifted
that cap to sixteen and admitted digits, which read `.0` as an extension, so `/api/v1.0` and
`/v3.0` stopped being reported at all; its path body is byte-identical to a5484ba's. 38fb601
never touched ROUTE_PATH. The other reads the printed contract rather than the rule behind it.

- FR-011 / AC-012 / OT-013 the route rule stops at a file and at a path with no segment
    test_files_are_not_routes_versions_are_and_a_bare_double_slash_is_neither
- FR-012 / FR-017 / GI-003 `types` names every class the lens reports, for the right reader
    test_types_states_the_contract_the_lens_enforces

One more test is measured against no revision of doctype.py at all, because none of them
changes its answer: the census it runs is ten at cfafe8e, 19941ad, 9859317 and 63e5e58 alike,
measured. What it is red against is this module's own docstring as 63e5e58 left it, which said
eight.

- FR-039 / AC-039 the rule count this docstring states is the script's own census
    test_the_rule_count_in_this_docstring_is_the_scripts_own_census

Every test that runs the script drives it through the conftest ``run_script`` helper (GI-004,
CT-007), and every assertion on a run quotes the captured exit code, stdout and stderr, because
a bare ``assert result.returncode == 1`` tells a reader nothing about which of ten rules
fired. The three source-reading tests named in the next paragraph are the exception to both
halves of that sentence, and the only exception to either: they start no process, so there is
no exit code to quote, and their assertions quote the source they read instead. The sentence
used to open "Every test", flatly, in the paragraph directly above the one that disclosed the
exceptions it was not true of.

That "ten" is the script's own census, and it is read rather than restated:
``test_the_rule_count_in_this_docstring_is_the_scripts_own_census`` takes the number out of the
sentence above and counts doctype.py's exit-1-capable rules against it. The sentence said
"eight" from a5484ba, the commit that wrote it, through seven later revisions of this file, and
the census was ten at every one of them — measured. Nine rule labels reach ``defects.append``
and ``undefined-jargon`` reaches the same list through ``check_jargon``, which returns its
findings in the defects slot for a `user` page; ``main`` exits 1 on any of the ten.

Three tests read ``scripts/doctype.py`` as text rather than running it. Two pin an allowlist
entry that could never match: an entry the matching regex cannot produce has no observable
behaviour to assert against in either direction — it excuses nothing and nothing is reported,
so the source is the only place the promise lives. The third is the census above, which has no
behaviour to assert against either, because the number it checks is a sentence in this
docstring and nothing a run prints. Nothing here imports doctype (GI-004); the census parses
the script with ``ast``, the way test_readme.py reads the scripts it checks.

No test uses ``@pytest.mark.skip`` or ``xfail``.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

# The script under test, as text, for the three source-reading tests. Computed here rather than
# imported from conftest: this module owns its own path and conftest belongs to the harness.
DOCTYPE_PY = Path(__file__).resolve().parent.parent / "scripts" / "doctype.py"

STUB_MARKER = "<!-- webster: not written yet -->"

# The skeleton scaffold.py writes. Its placeholder braces name a symbol, a route and a flag on
# purpose: AC-019 asks that a stub's prose produces no lens, jargon or grade finding, and a
# skeleton that named nothing forbidden would pass that test without proving anything. Built by
# concatenation rather than str.format, because the braces below are the content under test.
STUB_BODY = (
    f"\n{STUB_MARKER}\n\n"
    "## Overview\n\n"
    "{What `create_item` does at `/dashboard` when you pass `--verbose`.}\n"
)


def outcome(result) -> str:
    """The whole process, for an assertion that failed to explain itself."""
    return (f"exit {result.returncode}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}")


def write_page(docs: Path, name: str, frontmatter: str, body: str) -> Path:
    page = docs / name
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"---\n{frontmatter.strip()}\n---\n\n{body.lstrip()}", encoding="utf-8")
    return page


def write_stub(docs: Path, name: str, frontmatter: str) -> Path:
    return write_page(docs, name, frontmatter, f"# {name}\n" + STUB_BODY)


def check(run_script, docs: Path, **env: str):
    """``doctype.py check <docs>`` with the finding limit lifted.

    ``WEBSTER_SHOW_PER_RULE`` defaults to 6 and collapses the rest into "... and N more", which
    would make an assertion about the seventh wrong-lens finding fail for the wrong reason."""
    environment = {"WEBSTER_SHOW_PER_RULE": "50"}
    environment.update(env)
    return run_script("doctype.py", "check", docs, env=environment)


@pytest.fixture
def docs_dir(tmp_path: Path) -> Path:
    """An empty docs tree per test. Pages are written by the test that needs them, because the
    lens findings depend on the exact prose and a shared tree would hide which page produced
    which finding."""
    docs = tmp_path / "docs"
    docs.mkdir()
    return docs


def survey_json(tmp_path: Path, *, labels=(), screens=(), commands=()) -> Path:
    """A saved survey.py document, in the shape survey.py actually writes.

    user_surface.labels[] carry `text`, user_surface.commands[] carry `name`, and
    user_surface.screens[] carry `path` always and `name` only sometimes. Field paths rather
    than survey.py line numbers, and no account of how survey.py fills them: this docstring is
    a claim about another file that nothing re-reads, and the line numbers that used to be here
    all pointed at the wrong statement."""
    path = tmp_path / "webster-survey.json"
    path.write_text(json.dumps({
        "user_surface": {
            "labels": [{"text": t, "anchor": "src/ui/App.tsx:1"} for t in labels],
            "screens": [{"path": f"/{t.lower()}", "anchor": "src/ui/App.tsx:1", "name": t}
                        for t in screens],
            "commands": [{"name": t, "anchor": "src/cli/main.py:1"} for t in commands],
            "messages": [],
        },
    }), encoding="utf-8")
    return path


# -----------------------------------------------------------------------------
# FR-010 (OT-011, OT-012, AC-011): CODE_IDENT sees snake_case and acronym-initial
# PascalCase, and still leaves paths and environment variables alone
# -----------------------------------------------------------------------------
def test_snake_case_symbol_is_wrong_lens(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nThe form calls `create_item` when you save.\n")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, f"Expected exit 1 on a snake_case symbol.\n{outcome(result)}"
    assert "an internal symbol name, `create_item`" in result.stdout, (
        f"Expected `create_item` reported as an internal symbol.\n{outcome(result)}"
    )


def test_acronym_initial_pascal_is_wrong_lens(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nThe page builds an `APIClient` and talks to an `HTTPServer`.\n")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, f"Expected exit 1 on APIClient.\n{outcome(result)}"
    for symbol in ("`APIClient`", "`HTTPServer`"):
        assert f"an internal symbol name, {symbol}" in result.stdout, (
            f"Expected {symbol} reported; the old PascalCase branch rejected consecutive "
            f"capitals and let it through.\n{outcome(result)}"
        )


def test_path_and_env_var_are_not_symbols(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nEdit `my_config.yaml` and read `DATABASE_URL` there.\n")

    result = check(run_script, docs_dir)

    assert "internal symbol name" not in result.stdout, (
        f"A filename and an all-caps name are not symbols: the first holds a '.', the second "
        f"belongs to ENV_VAR.\n{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"Expected exit 0 on a page naming only a filename and a variable.\n{outcome(result)}"
    )


def test_all_caps_with_a_digit_is_not_an_internal_symbol(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nWe speak `HTTP2`, hash with `SHA256` or `MD5`, run on `EC2` "
               "and store text as `UTF8`.\n")

    result = check(run_script, docs_dir)

    assert "internal symbol name" not in result.stdout, (
        f"FR-010 excludes ALL-CAPS because that is ENV_VAR\'s job. Widening the branches to "
        f"reach `APIClient` let a digit stand in for the lowercase tail, so a token with no "
        f"lowercase letter in it at all was reported as a symbol.\n{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"Expected exit 0 on a page naming only all-caps tokens.\n{outcome(result)}"
    )


def test_versioned_standard_names_are_not_internal_symbols(run_script, docs_dir):
    """FR-010 / FR-015 / AC-011: the version digit on a public standard is not a symbol.

    IDENT_ALLOW is compared exactly, so `OAuth` in the set did nothing for the `OAuth2` a page
    actually writes, and `IPv4` was in neither the set nor the narrow CODE_IDENT that preceded
    the widening. Both arrived as findings only once CODE_IDENT learned to open on an acronym,
    which is why this is measured against 9859317 rather than 2abe081."""
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nThe address is `IPv4` or `IPv6`, and you sign in with `OAuth2` "
               "or `OAuth1`. None of that lets the page call `getUser`.\n")

    result = check(run_script, docs_dir)

    for name in ("`IPv4`", "`IPv6`", "`OAuth2`", "`OAuth1`"):
        assert f"an internal symbol name, {name}" not in result.stdout, (
            f"{name} names a public standard, which a user page is allowed to do. The exact "
            f"compare punished the versioned spelling of a name the set already "
            f"allowed.\n{outcome(result)}"
        )
    assert "an internal symbol name, `getUser`" in result.stdout, (
        f"The control: the symbol half of the lens still fires, so the four assertions above "
        f"mean something.\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"Expected exit 1 from `getUser` alone.\n{outcome(result)}"
    )


# -----------------------------------------------------------------------------
# FR-011 (OT-013, AC-012): ROUTE_PATH matches any backticked path, minus file-ish
# -----------------------------------------------------------------------------
def test_plain_route_is_wrong_lens(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen `/dashboard` and then `/settings/profile`.\n")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, f"Expected exit 1 on a plain route.\n{outcome(result)}"
    for route in ("/dashboard", "/settings/profile"):
        assert f"a request route, {route}" in result.stdout, (
            f"Expected {route} reported; ROUTE_PATH used to match only /api and /v<n>."
            f"\n{outcome(result)}"
        )


def test_path_with_a_file_extension_is_not_a_route(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nYour settings live in `/etc/hosts.conf` on that machine.\n")

    result = check(run_script, docs_dir)

    assert "a request route" not in result.stdout, (
        f"A path whose last segment carries a file extension is a file a reader may really "
        f"have to open, not a route.\n{outcome(result)}"
    )
    assert result.returncode == 0, f"Expected exit 0.\n{outcome(result)}"


def test_files_are_not_routes_versions_are_and_a_bare_double_slash_is_neither(
        run_script, docs_dir):
    """Where the route rule stops: at a file, and at a path with no segment in it.

    ``.conf`` is four letters and fitted the old six-letter cap. ``.properties`` is ten,
    ``.markdown`` is eight, ``.template`` is eight, and ``/.gitignore`` is a dotfile with no
    stem at all — every one of them was reported to a reader as naming a request route, on the
    page that told them where their settings live.

    Lifting that cap took one more thing with it that this pins as well. eb27489 admitted
    digits into the extension, which made `.0` look like one, so `/api/v1.0` and `/v3.0`
    stopped being reported — routes the narrow /api and /v<n> rule a5484ba replaced had caught
    since ROUTE_PATH was written, which made a fix into a regression. The `//` gap belongs to
    a5484ba rather than to the cap-lifting commit: its path body admitted every character but a
    backtick or a space, a second slash included, and eb27489 left that body byte-identical.
    That made `` `//` `` a path with zero segments when A-008 asks for at least one.

    The plain route on the same page fires throughout, so each assertion below pins its own
    exclusion rather than the whole rule going quiet."""
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen `/settings/profile` to change your name. The app keeps its "
               "keys in\n`/etc/app.properties`, its notes in `/docs/readme.markdown`, its "
               "ignore list in\n`/.gitignore` and its letter in `/x.template`. It talks to "
               "`/api/v1.0` and\n`/v3.0`, and it writes `//` when it means nothing at all.\n")

    result = check(run_script, docs_dir)

    for path in ("/etc/app.properties", "/docs/readme.markdown", "/.gitignore", "/x.template"):
        assert f"a request route, {path}" not in result.stdout, (
            f"{path} is a file a reader may really have to open. The extension exclusion used "
            f"to be capped at six letters, so this one was over it.\n{outcome(result)}"
        )
    assert "a request route, //" not in result.stdout, (
        f"`//` names no segment, and A-008 asks a route for at least one. The path body used "
        f"to admit a slash, so an empty root was reported as a route.\n{outcome(result)}"
    )
    for route in ("/settings/profile", "/api/v1.0", "/v3.0"):
        assert f"a request route, {route}" in result.stdout, (
            f"{route} is a route a user page must not name. A version in the last segment is "
            f"not a file extension, and admitting digits to reach `.7z` used to make it look "
            f"like one.\n{outcome(result)}"
        )
    assert result.returncode == 1, (
        f"Expected exit 1 from the routes alone.\n{outcome(result)}"
    )


# -----------------------------------------------------------------------------
# FR-012 (OT-014, AC-013): flags on a user page, unless the allowlist excuses them
# -----------------------------------------------------------------------------
def test_flags_are_wrong_lens_on_a_user_page(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nAdd `--verbose`, or `-v` if you prefer the short form.\n")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, f"Expected exit 1 on a flag.\n{outcome(result)}"
    for flag in ("`--verbose`", "`-v`"):
        assert f"a command-line flag, {flag}" in result.stdout, (
            f"Expected {flag} reported on a user page.\n{outcome(result)}"
        )


def test_flag_named_in_lens_allow_is_not_reported(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nAdd `--verbose` to see more.\n")

    result = check(run_script, docs_dir, WEBSTER_LENS_ALLOW="--verbose")

    assert "command-line flag" not in result.stdout, (
        f"WEBSTER_LENS_ALLOW is the declaration that this product's readers really do type "
        f"this flag.\n{outcome(result)}"
    )
    assert result.returncode == 0, f"Expected exit 0.\n{outcome(result)}"


# -----------------------------------------------------------------------------
# FR-017 / GI-003 (OT-015, AC-014): an operator holds a terminal; routes and flags
# fire only for `user`, while symbols and architecture keep firing for both
# -----------------------------------------------------------------------------
def test_operator_page_is_not_reported_for_a_route_or_a_flag(run_script, docs_dir):
    write_page(docs_dir, "run.md", "title: Run\ndoc_type: how-to\naudience: operator",
               "# Run\n\nPoll `/api/health` and run the check with `--verbose`.\n")

    result = check(run_script, docs_dir)

    assert "wrong-lens" not in result.stdout, (
        f"LENS_MAY_NOT['operator'] forbids internal symbols and architecture, not the routes "
        f"and flags an operator's whole job is made of.\n{outcome(result)}"
    )
    assert result.returncode == 0, f"Expected exit 0 on an operator page.\n{outcome(result)}"


def test_operator_page_is_still_reported_for_a_symbol(run_script, docs_dir):
    write_page(docs_dir, "run.md", "title: Run\ndoc_type: how-to\naudience: operator",
               "# Run\n\nPoll `/api/health`, pass `--verbose`, and never call `getUser`.\n")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, f"Expected exit 1 on the symbol.\n{outcome(result)}"
    assert "an internal symbol name, `getUser`" in result.stdout, (
        f"Gating routes and flags must not switch off CODE_IDENT for an operator."
        f"\n{outcome(result)}"
    )
    assert "a request route" not in result.stdout, (
        f"The route on the same page stays silent.\n{outcome(result)}"
    )
    assert "a command-line flag" not in result.stdout, (
        f"The flag on the same page stays silent.\n{outcome(result)}"
    )


def test_architecture_is_reported_for_an_operator_and_for_a_user(run_script, docs_dir):
    """GI-003 / FR-017: the half of the clause the audience gate must not take with it.

    "ROUTE_PATH and the new FLAG regex fire only when audience == 'user'; CODE_IDENT and
    ARCH_HARD keep firing for operator." Gating routes and flags meant putting two of the five
    rules check_lens runs over each line behind an `if` on the audience, leaving CODE_IDENT and
    ARCH_HARD outside it, and the ARCH_HARD half of that sentence had no test at all: the gate
    could be widened to swallow architecture and this section's header would still have claimed
    both readers were covered. The two pages carry the same two terms in the same sentence and
    declare different audiences — the file name and the title differ with them — so each
    assertion below is the same prose read by the reader named in it."""
    write_page(docs_dir, "run.md", "title: Run\ndoc_type: how-to\naudience: operator",
               "# Run\n\nThe middleware logs each call and the service layer keeps a queue.\n")
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nThe middleware logs each call and the service layer keeps a queue.\n")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, (
        f"Architecture is forbidden to both readers: LENS_MAY_NOT names it on the user line "
        f"and on the operator line.\n{outcome(result)}"
    )
    for audience in ("operator", "user"):
        for term in ("middleware", "service layer"):
            assert f"a '{audience}' page names part of the architecture, {term}" in \
                result.stdout, (
                    f"ARCH_HARD keeps firing for a '{audience}' page. The route and flag gate "
                    f"sits inside the same loop, and nothing here was pinned before."
                    f"\n{outcome(result)}"
                )
    assert "a request route" not in result.stdout and "a command-line flag" not in \
        result.stdout, (
            f"The control on the gate itself: neither page names a route or a flag, so the "
            f"four assertions above are about architecture alone.\n{outcome(result)}"
        )


def test_types_states_the_contract_the_lens_enforces(run_script):
    """``doctype.py types`` is where a writer reads the lens before the lens reads them.

    It printed "may not name internal symbols, routes, environment variables and architecture"
    for a `user` page while check_lens had already grown the flag rule and widened routes past
    the /api and /v<n> prefixes. A writer read that list, wrote `--verbose`, and got a
    wrong-lens defect for a class the tool had just told them was allowed — the one failure a
    printed contract exists to prevent.

    All five kinds check_lens can report, against all three audiences, rather than the two
    kinds and the two audiences this test first read: an omission from the printed contract is
    the same failure whichever class goes missing, and internal symbols, environment variables
    and architecture were unasserted while the index above claimed the test covered every
    class. Each audience's third line is read on its own rather than its block joined, because
    the operator's `assumes` line names environment variables as something an operator
    handles — joined, the negative assertion for that reader would have read the wrong
    line."""
    result = run_script("doctype.py", "types")

    assert result.returncode == 0, f"Expected exit 0 from `types`.\n{outcome(result)}"
    lines = result.stdout.splitlines()
    contracts = {}
    for i, line in enumerate(lines):
        m = re.match(r"\s\s(user|operator|developer)\s+grade\s", line)
        if m:
            contracts[m.group(1)] = lines[i + 2].strip()
    assert set(contracts) == {"user", "operator", "developer"}, (
        f"`types` prints one three-line block per audience — the reader line, the assumes "
        f"line, and the contract. Found blocks for {sorted(contracts)}.\n{outcome(result)}"
    )

    # The five kinds check_lens appends to `named`, against the readers each one fires on.
    # CODE_IDENT and ARCH_HARD run for every audience LENS_MAY_NOT names a contract for;
    # ROUTE_PATH and FLAG run inside the `audience == "user"` gate (FR-017, GI-003). ENV_VAR
    # reaches a `user` page only as well, but by a condition of its own on the match rather
    # than by that gate, so widening the gate would not be what let it out.
    # `developer` has no contract to keep, so no kind may appear on its line either.
    fires_on = {
        "internal symbols": ("user", "operator"),
        "architecture": ("user", "operator"),
        "environment variable": ("user",),
        "route": ("user",),
        "flag": ("user",),
    }
    for kind, readers in fires_on.items():
        for audience in ("user", "operator", "developer"):
            if audience in readers:
                assert kind in contracts[audience], (
                    f"check_lens reports {kind} on a '{audience}' page and the printed "
                    f"contract for that reader never says so. Got: {contracts[audience]}"
                    f"\n{outcome(result)}"
                )
            else:
                assert kind not in contracts[audience], (
                    f"check_lens never reports {kind} on a '{audience}' page, so the printed "
                    f"contract for that reader must not claim it. Got: "
                    f"{contracts[audience]}\n{outcome(result)}"
                )


# -----------------------------------------------------------------------------
# FR-015 (OT-016, AC-015): IDENT_ALLOW compares lowercased, with real spellings
# -----------------------------------------------------------------------------
def test_product_names_are_not_internal_symbols(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nWorks on `iPhone`, stores in `PostgreSQL`, speaks `GraphQL`.\n")

    result = check(run_script, docs_dir)

    assert "internal symbol name" not in result.stdout, (
        f"The set used to hold `IPhone` and compare case-sensitively, so the `iPhone` a page "
        f"actually writes was reported as an internal.\n{outcome(result)}"
    )
    assert result.returncode == 0, f"Expected exit 0.\n{outcome(result)}"


def test_no_ident_allow_entry_is_all_caps():
    """FR-015 / AC-011: an entry CODE_IDENT cannot produce excuses nothing.

    CODE_IDENT asserts one lowercase letter anywhere in the token (_HAS_LOWER) so that `SHA256`
    and `EC2` stay the acronym check's business. An IDENT_ALLOW entry with no lowercase letter
    in it can therefore never be produced, which is the same dead weight FR-041 removed from
    UNIVERSAL_ACRONYMS. HTTP2 and HTTP3 are what this pins out: they read like the versioned
    standards `IPv4` and `OAuth2` added beside them, and unlike those two they can never reach
    this set. The set once held `IOS` for the same reason.

    A necessary condition, not a sufficient one — that an entry contains a lowercase letter
    does not prove CODE_IDENT matches it. The behavioural half is
    test_all_caps_with_a_digit_is_not_an_internal_symbol, which proves the regex really does
    ignore a token without one."""
    source = DOCTYPE_PY.read_text(encoding="utf-8")
    start = source.index("IDENT_ALLOW = {name.lower() for name in (")
    body = source[start:source.index("\n)}", start)]

    entries = set()
    for line in body.splitlines():
        entries.update(re.findall(r'"([^"]+)"', line.split("#", 1)[0]))

    assert entries, f"Found no entries in the IDENT_ALLOW literal at {DOCTYPE_PY}"
    assert {"IPv4", "OAuth2"} <= entries, (
        f"`IPv4` and `OAuth2` were reported as internal symbols on a user page naming a public "
        f"standard, because the compare is exact and only the unversioned `OAuth` was here. "
        f"Found: {sorted(entries)}"
    )
    no_lowercase = sorted(e for e in entries if not any(c.islower() for c in e))
    assert not no_lowercase, (
        f"CODE_IDENT's _HAS_LOWER lookahead requires one lowercase letter in the token, so "
        f"these entries can never be produced and allow nothing: {no_lowercase}"
    )


# -----------------------------------------------------------------------------
# FR-013 / CT-003 (OT-017, AC-016): WEBSTER_SURVEY extends the allowlist
# -----------------------------------------------------------------------------
def test_without_the_survey_the_same_label_is_reported(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen `DataSources` from the sidebar.\n")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, f"Expected exit 1 without a survey.\n{outcome(result)}"
    assert "an internal symbol name, `DataSources`" in result.stdout, (
        f"This is the control for the next test: without the survey the product's own label "
        f"looks exactly like an internal symbol.\n{outcome(result)}"
    )


def test_survey_labels_extend_the_allowlist(run_script, docs_dir, tmp_path):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen `DataSources` from the sidebar.\n")
    survey = survey_json(tmp_path, labels=("DataSources",))

    result = check(run_script, docs_dir, WEBSTER_SURVEY=str(survey))

    assert "internal symbol name" not in result.stdout, (
        f"user_surface.labels is the product saying this word is on its own screen."
        f"\n{outcome(result)}"
    )
    assert result.returncode == 0, f"Expected exit 0 with the survey.\n{outcome(result)}"


def test_survey_screen_names_and_commands_extend_the_allowlist(run_script, docs_dir, tmp_path):
    """FR-013 / CT-003: screens.name and commands feed the same allowlist as labels, and the
    two sides meet lowercased.

    The survey spells the screen `auditLog` where the page writes `AuditLog`. Both spellings
    are tokens CODE_IDENT reports — camelCase and PascalCase are two of its branches — so the
    lowercasing on either side is load-bearing here and dropping it turns this test red. The
    comment that used to stand on the line below named `auditlog` as the spelling the page
    might use instead: an all-lowercase token carrying no underscore matches no branch of
    CODE_IDENT, so it is never reported with a survey or without one, and the allowlist could
    not have been what excused it."""
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen `AuditLog`, then run `--export` on it.\n")
    survey = survey_json(tmp_path, screens=("auditLog",), commands=("--export",))

    result = check(run_script, docs_dir, WEBSTER_SURVEY=str(survey))

    assert "wrong-lens" not in result.stdout, (
        f"screens.name and commands feed the same allowlist as labels, and the `auditLog` in "
        f"the survey has to reach the `AuditLog` on the page.\n{outcome(result)}"
    )
    assert result.returncode == 0, f"Expected exit 0.\n{outcome(result)}"


def test_a_screen_name_reaches_the_lens_and_a_screen_path_does_not(
    run_script, docs_dir, tmp_path
):
    """The two screen shapes, against the three sources FR-013 names. A screen carrying a
    `name` contributes that name even when it arrives spaced ("Data Sources") and the page
    backticks it closed up (`DataSources`). A screen carrying only a `path` contributes
    nothing: neither the path nor its file stem is one of the three, so a page naming the stem
    is still reported. Built here rather than through ``survey_json``, which always sets a name
    equal to the term under test and so could never separate the two shapes."""
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen `DataSources`, which the app builds from `DataSourcesPage`.\n")
    survey = tmp_path / "screens-only.json"
    survey.write_text(json.dumps({"user_surface": {
        "labels": [],
        "screens": [
            {"path": "component:DataSourcesPage", "name": "Data Sources",
             "anchor": "src/pages/DataSourcesPage.tsx:1"},
            {"path": "src/pages/DataSourcesPage.tsx", "anchor": "src/App.tsx:12"},
        ],
        "commands": [],
        "messages": [],
    }}), encoding="utf-8")

    result = check(run_script, docs_dir, WEBSTER_SURVEY=str(survey))

    assert "an internal symbol name, `DataSources`" not in result.stdout, (
        f"screens.name is one of the three sources; spaced, it can only reach the lens with "
        f"its whitespace removed.\n{outcome(result)}"
    )
    assert "an internal symbol name, `DataSourcesPage`" in result.stdout, (
        f"The path-only screen names no term the allowlist may take. Reading its file stem "
        f"would be a fourth source, and FR-013 names three.\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"Expected exit 1: the named screen is excused, the path-only screen is not."
        f"\n{outcome(result)}"
    )


# -----------------------------------------------------------------------------
# FR-035 (OT-017, AC-017): the header line says what the survey contributed, and a
# survey that cannot be read is a smaller allowlist rather than a crash
# -----------------------------------------------------------------------------
def test_header_line_counts_the_terms_the_survey_contributed(run_script, docs_dir, tmp_path):
    """FR-035: the number in the header is the size of the allowlist the survey contributed.

    Two surveys of different sizes, run one after the other against the same page. This test
    used to assert the single literal ``lens allowlist: 3 terms from WEBSTER_SURVEY`` against
    a fixture holding exactly three terms, which a constant 3 printed by the header satisfies
    — and a count that is not read from the allowlist is the whole of what FR-035 forbids.

    Every term below is a single word, and ``load_survey_allow`` adds each term twice: once as
    given and once with its whitespace removed, which for a single word is the same string.
    The expected count is therefore the length of the tuple, taken from the fixture rather
    than written out beside it."""
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nA page that names nothing forbidden.\n")
    small = ("DataSources", "AuditLog")
    large = ("DataSources", "AuditLog", "Billing", "Retention", "Exports")
    small_dir = tmp_path / "small"
    small_dir.mkdir()
    large_dir = tmp_path / "large"
    large_dir.mkdir()

    for survey, terms in ((survey_json(small_dir, labels=small), small),
                          (survey_json(large_dir, labels=large), large)):
        result = check(run_script, docs_dir, WEBSTER_SURVEY=str(survey))

        assert f"lens allowlist: {len(terms)} terms from WEBSTER_SURVEY" in result.stdout, (
            f"The header counts the terms this survey contributed, and this one holds "
            f"{len(terms)}: {list(terms)}. Two surveys of different sizes, because one "
            f"fixture cannot tell a real count from a constant.\n{outcome(result)}"
        )
        assert str(survey) in result.stdout, (
            f"Expected the header line to name which file was read.\n{outcome(result)}"
        )


def test_missing_survey_file_does_not_crash(run_script, docs_dir, tmp_path):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nA page that names nothing forbidden.\n")

    result = check(run_script, docs_dir, WEBSTER_SURVEY=str(tmp_path / "not-there.json"))

    assert result.returncode == 0, (
        f"A missing survey is an optional input that was not there, not a failure."
        f"\n{outcome(result)}"
    )
    assert "no survey allowlist" in result.stdout, (
        f"Expected the header line to say no survey allowlist was loaded.\n{outcome(result)}"
    )
    assert "Traceback" not in result.stderr, f"Expected no traceback.\n{outcome(result)}"


def test_malformed_survey_file_does_not_crash(run_script, docs_dir, tmp_path):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen `DataSources` from the sidebar.\n")
    broken = tmp_path / "broken.json"
    broken.write_text("{\"user_surface\": [truncated", encoding="utf-8")

    result = check(run_script, docs_dir, WEBSTER_SURVEY=str(broken))

    assert "no survey allowlist" in result.stdout, (
        f"Expected the header line to say the survey was not loaded.\n{outcome(result)}"
    )
    assert result.returncode == 1, (
        f"Unreadable means no extra allow, so the label is still reported — the run completes "
        f"and reports what it can see.\n{outcome(result)}"
    )
    assert "Traceback" not in result.stderr, f"Expected no traceback.\n{outcome(result)}"


# -----------------------------------------------------------------------------
# FR-016 / FR-041 / FR-033 (OT-018, AC-018): API and CLI are universal; the set
# holds nothing the ACRONYM regex cannot reach
# -----------------------------------------------------------------------------
def test_api_and_cli_are_not_undefined_jargon(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nThe API returns your items.\nThe CLI prints them for you.\n"
               "Then ZQX arrives in the mail.\n")

    result = check(run_script, docs_dir)

    assert "'API' is used without ever being expanded" not in result.stdout, (
        f"Expanding API reads as condescension.\n{outcome(result)}"
    )
    assert "'CLI' is used without ever being expanded" not in result.stdout, (
        f"Expanding CLI reads as condescension.\n{outcome(result)}"
    )
    assert "'ZQX' is used without ever being expanded" in result.stdout, (
        f"The control: the jargon check still runs, so the two assertions above mean "
        f"something.\n{outcome(result)}"
    )


def test_every_universal_acronym_is_reachable_by_the_acronym_regex():
    """FR-041 removes an entry that could never match, which has no observable behaviour.

    Reading the source is the only way to pin it. WARNING is seven characters against ACRONYM's
    six-character ceiling, so the regex could never produce it: the entry excused a word the
    check cannot raise, and no run ever reported it. Widening the regex to reach it would have
    made every seven-letter capitalised word an acronym candidate, so A-034 keeps the ceiling
    and drops the entry instead."""
    source = DOCTYPE_PY.read_text(encoding="utf-8")
    start = source.index("UNIVERSAL_ACRONYMS = {")
    body = source[start:source.index("\n}", start)]

    entries = set()
    for line in body.splitlines():
        entries.update(re.findall(r'"([^"]+)"', line.split("#", 1)[0]))

    assert entries, f"Found no entries in the UNIVERSAL_ACRONYMS literal at {DOCTYPE_PY}"
    assert "WARNING" not in entries, (
        "WARNING is seven characters against ACRONYM's six-character ceiling, so it could "
        "never match: the entry promised to excuse a word this check cannot raise (FR-041)."
    )
    unreachable = sorted(e for e in entries if not 2 <= len(e) <= 6)
    assert not unreachable, (
        f"ACRONYM matches 2 to 6 characters, so these entries are promises the check cannot "
        f"keep: {unreachable}"
    )
    assert re.search(r"ACRONYM = re\.compile\(r\"\\b\(\[A-Z\]\[A-Z0-9\]\{1,5\}\)s\?\\b\"\)",
                     source), (
        "FR-033/FR-041 keep the ACRONYM bound at 2 to 6 characters; the fork was resolved by "
        "removing WARNING, not by widening the regex."
    )


# -----------------------------------------------------------------------------
# FR-014 (OT-019, AC-019): a stub gets the frontmatter checks and nothing else
# -----------------------------------------------------------------------------
def test_stub_without_audience_is_reported(run_script, docs_dir):
    write_stub(docs_dir, "items.md", "title: Items\ndoc_type: explanation")
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nA written page, so the tree is not all stubs.\n")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, (
        f"A page nobody has written yet still has to say who it is for.\n{outcome(result)}"
    )
    assert "no-audience" in result.stdout and "items.md" in result.stdout, (
        f"Expected the no-audience defect on the stub; doctype.py used to `continue` past "
        f"every check on a stub.\n{outcome(result)}"
    )


def test_stub_placeholder_prose_produces_no_other_finding(run_script, docs_dir):
    write_stub(docs_dir, "items.md", "title: Items\ndoc_type: explanation")
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nA written page, so the tree is not all stubs.\n")

    result = check(run_script, docs_dir)

    # STUB_BODY names `create_item`, `/dashboard` and `--verbose` inside its placeholder braces.
    for rule in ("wrong-lens", "undefined-jargon", "reading-grade", "type-mixing"):
        assert rule not in result.stdout, (
            f"A skeleton's placeholder braces are not writing; reporting {rule} on them "
            f"teaches the reader to ignore the report.\n{outcome(result)}"
        )


def test_stub_with_an_unknown_audience_or_type_is_reported(run_script, docs_dir):
    write_stub(docs_dir, "items.md", "title: Items\ndoc_type: recipe\naudience: robot")
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nA written page, so the tree is not all stubs.\n")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, f"Expected exit 1.\n{outcome(result)}"
    assert "audience 'robot' is not a known reader" in result.stdout, (
        f"unknown-audience is a frontmatter check and runs on a stub.\n{outcome(result)}"
    )
    assert "doc_type 'recipe' is not a known type" in result.stdout, (
        f"unknown-type is a frontmatter check and runs on a stub.\n{outcome(result)}"
    )


# -----------------------------------------------------------------------------
# FR-014 / FR-036 / FR-040 / CT-004 (OT-020, OT-042): an all-stub tree was not
# checked, and a defect on a stub beats not-checked
# -----------------------------------------------------------------------------
def test_tree_of_only_stubs_exits_two_with_nothing_to_check(run_script, docs_dir):
    write_stub(docs_dir, "items.md", "title: Items\ndoc_type: explanation\naudience: user")
    write_stub(docs_dir, "export.md", "title: Export\ndoc_type: how-to\naudience: operator")

    result = check(run_script, docs_dir)

    assert result.returncode == 2, (
        f"A tree of skeletons resolved every rule it had, which is not the same as being "
        f"checked; it used to exit 0.\n{outcome(result)}"
    )
    assert "2 stubs, nothing to check" in result.stdout, (
        f"Expected the nothing-to-check line, mirroring drift.py's no_anchors."
        f"\n{outcome(result)}"
    )
    assert "matches its declared type" not in result.stdout, (
        f"Nothing was matched against a declared type, so the pass line must not print."
        f"\n{outcome(result)}"
    )


def test_empty_docs_tree_is_not_checked(run_script, docs_dir):
    result = check(run_script, docs_dir)

    assert result.returncode == 2, (
        f"CT-004: exit 0 is never possible with zero pages checked. The gate asked for "
        f"stubs > 0, so a tree holding no page at all — a fresh docs directory, or one whose "
        f"only files are README.md and llms.txt — fell past it onto the pass line."
        f"\n{outcome(result)}"
    )
    assert "0 stubs, nothing to check" in result.stdout, (
        f"The FR-036 line with N=0. Zero stubs and zero pages is still nothing to check."
        f"\n{outcome(result)}"
    )
    assert "matches its declared type" not in result.stdout, (
        f"There was no page to match against a declared type.\n{outcome(result)}"
    )


def test_all_stub_tree_with_a_defect_exits_one(run_script, docs_dir):
    write_stub(docs_dir, "items.md", "title: Items\ndoc_type: explanation\naudience: user")
    write_stub(docs_dir, "export.md", "title: Export\ndoc_type: how-to")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, (
        f"A frontmatter defect on a stub is a real finding: FR-040 reserves 'nothing to "
        f"check' for zero non-stub pages AND zero defects.\n{outcome(result)}"
    )
    assert "no-audience" in result.stdout, (
        f"Expected the no-audience defect to be reported, not swallowed by the exit 2 path."
        f"\n{outcome(result)}"
    )
    assert "nothing to check" not in result.stdout, (
        f"The nothing-to-check line prints only when no defect was found (FR-036)."
        f"\n{outcome(result)}"
    )


def test_the_pass_line_names_the_written_pages_it_matched(run_script, docs_dir):
    """AC-020 / AC-021 / FR-014: the sentence a passing run ends on, over the pages it read.

    A stub declares a doc_type of its own and is never matched against it: run_check counts it
    in `stubs` and `continue`s before check_typed. "every page matches its declared type"
    therefore spoke for a population the run had not typed, and the only guards this file had
    on that sentence were the two that assert it does not print at all — the all-stub tree and
    the empty tree. A tree with no stub in it prints the sentence as well, and there it was
    exactly true: the branch is reached only with `untyped` empty, so every page such a run
    counted did reach check_typed. The mixed tree, the ordinary shape of a docs directory part
    way through being written, prints it over a page the run never typed, and no assertion in
    this file had read it there.

    The second run is what makes the first one's silence visible: the same stub with its
    marker deleted and no other byte changed reports the three tokens its placeholder braces
    name. A sentence that had been true of every page in the tree would have had nothing left
    to find there."""
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen the app and choose a store from the list.\n")
    write_stub(docs_dir, "items.md", "title: Items\ndoc_type: how-to\naudience: user")

    result = check(run_script, docs_dir)

    assert result.returncode == 0, (
        f"One clean written page and one well-formed stub is a passing run (AC-021)."
        f"\n{outcome(result)}"
    )
    assert "every written page matches its declared type" in result.stdout, (
        f"The pass line has to name the population the run measured: the pages read as "
        f"writing, which are the only ones check_typed saw.\n{outcome(result)}"
    )
    assert "1 stubs were not matched against theirs" in result.stdout, (
        f"The stub count rides beside the sentence, so the reader can see what it is not "
        f"about.\n{outcome(result)}"
    )

    stub = docs_dir / "items.md"
    stub.write_text(stub.read_text(encoding="utf-8").replace(STUB_MARKER, ""), encoding="utf-8")

    written = check(run_script, docs_dir)

    assert written.returncode == 1, (
        f"The same page without the stub marker, and nothing else changed."
        f"\n{outcome(written)}"
    )
    for named in ("`create_item`", "/dashboard", "`--verbose`"):
        assert named in written.stdout, (
            f"{named} is inside the skeleton's placeholder braces. The run above reported "
            f"none of the three and still said every page matched, which is the claim this "
            f"test narrows.\n{outcome(written)}"
        )


def test_one_written_page_among_stubs_exits_by_that_page(run_script, docs_dir):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen the app and choose a store from the list.\n")
    for name in ("items.md", "export.md", "audit.md"):
        write_stub(docs_dir, name, f"title: {name}\ndoc_type: explanation\naudience: user")

    result = check(run_script, docs_dir)

    assert result.returncode == 0, (
        f"One written page is something to check, so the run exits by that page's findings "
        f"rather than by the stub count (AC-021).\n{outcome(result)}"
    )
    assert "nothing to check" not in result.stdout, (
        f"There was something to check.\n{outcome(result)}"
    )
    # The header's two populations, read off the tree on disk rather than off a literal chosen
    # to match them. `stubs += 1` and its `continue` sit above `pages += 1` in run_check, so a
    # stub reaches neither check_typed nor the page count and the header prints it in a field
    # of its own: a sentence claiming a stub is counted among the pages turns this red. Read up
    # to the bare word `stubs`, because what follows it is not the same string at cfafe8e —
    # "stubs skipped" there, "stubs (frontmatter only)" here — and this test is a control that
    # has to pass on both sides.
    header = re.search(r"^(\d+) pages checked, \d+ against a declared type, (\d+) stubs",
                       result.stdout, re.M)
    assert header, (
        f"doctype.py opens on the counts line, and this test reads it.\n{outcome(result)}"
    )
    md = sorted(docs_dir.rglob("*.md"))
    skeletons = [p for p in md if STUB_MARKER in p.read_text(encoding="utf-8")]
    assert (int(header.group(1)), int(header.group(2))) == (len(md) - len(skeletons),
                                                            len(skeletons)), (
        f"The header counts the pages read as writing and the stubs separately. This tree "
        f"holds {len(md) - len(skeletons)} written page and {len(skeletons)} stubs; the "
        f"header said {header.group(1)} and {header.group(2)}.\n{outcome(result)}"
    )


# -----------------------------------------------------------------------------
# FR-039 / AC-039: the rule count this module states is read off the script
# -----------------------------------------------------------------------------
def exit_one_rules() -> dict[str, str]:
    """Every rule label doctype.py can exit 1 on, mapped to the function that appends it.

    A rule reaches the exit-1 slot when it is appended to a list the enclosing function returns
    FIRST, because every checker in doctype.py returns its defects first and ``run_check`` adds
    that first element to ``defects``. That is what counts ``check_jargon``'s ``findings``, which
    is not called ``defects`` and is returned first on a `user` page, and what leaves the
    advisory labels out: ``advisories`` is only ever returned second.

    Parsed with ``ast`` rather than matched with a regex. The appended dicts hold f-strings with
    braces in them, so a text scan would depend on key order and on where the literal happens to
    wrap, and a reflow would change the census without changing a rule."""
    source = DOCTYPE_PY.read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        returned_first = {
            node.value.elts[0].id
            for node in ast.walk(fn)
            if isinstance(node, ast.Return)
            and isinstance(node.value, ast.Tuple)
            and node.value.elts
            and isinstance(node.value.elts[0], ast.Name)
        }
        for node in ast.walk(fn):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "append"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in returned_first
                    and node.args
                    and isinstance(node.args[0], ast.Dict)):
                continue
            for key, value in zip(node.args[0].keys, node.args[0].values):
                if (isinstance(key, ast.Constant) and key.value == "rule"
                        and isinstance(value, ast.Constant)):
                    found[value.value] = fn.name
    return found


def test_the_rule_count_in_this_docstring_is_the_scripts_own_census():
    """FR-039 / AC-039: a count this module states about the script is read off the script.

    The sentence above said "which of eight rules fired" from a5484ba, the commit that wrote
    it, through seven later revisions of this file, while doctype.py could exit 1 on ten —
    measured at cfafe8e, 19941ad, 9859317 and 63e5e58, ten at all four. Nothing read the
    sentence, which is how a number written once stayed wrong through every later revision of
    this file until the commit that added this test.

    Neither half is restated here. The number comes out of this module's docstring and the
    rules come out of doctype.py, so a rule added to the script without a hand on this
    docstring fails on the comparison rather than passing over a stale number."""
    doc = __doc__ or ""
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
             "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
             "fourteen": 14, "fifteen": 15, "sixteen": 16}
    stated = re.findall(r"which of (\S+) rules fired", re.sub(r"\s+", " ", doc))
    assert len(stated) == 1, (
        "The sentence this test reads is gone or duplicated. A census with nothing to compare "
        f"against measures nothing, so it has to be rewritten rather than left passing; found "
        f"{stated} in the docstring of {__file__}"
    )
    assert stated[0] in words, (
        f"'which of {stated[0]} rules fired' does not state a number this test can read, so "
        "the count it gives a reader is unchecked"
    )

    rules = exit_one_rules()
    assert words[stated[0]] == len(rules), (
        f"The docstring says doctype.py exits 1 on {stated[0]} ({words[stated[0]]}) rules; "
        f"{DOCTYPE_PY} has {len(rules)}:\n"
        + "\n".join(f"  {rule}  ({fn})" for rule, fn in sorted(rules.items()))
    )
    assert "undefined-jargon" in rules, (
        "The census missed check_jargon, whose findings are returned in the defects slot for a "
        f"`user` page and are as much an exit 1 as any other rule. Found: {sorted(rules)}"
    )
    assert "reading-grade" not in rules and "lens-drift" not in rules, (
        "The census swept up an advisory. An advisory never reaches exit 1 — `main` returns "
        f"`1 if defects else 0` — so counting one overstates the number. Found: {sorted(rules)}"
    )
