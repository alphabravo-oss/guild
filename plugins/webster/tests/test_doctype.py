"""Tests for plugins/webster/scripts/doctype.py — the lens, the allowlists, the stubs.

One red-to-green test per fix (US-010, AC-039). Measured against commit cfafe8e, the pre-change
baseline AC-039 names, as the sibling modules do: 19 of the 25 tests in the first index below
fail there and all 25 pass after. The measurement was taken at 2abe081 — this suite's own first
commit, which is where these tests could first be run — and it stands for cfafe8e because
``git show cfafe8e:plugins/webster/scripts/doctype.py`` and the same command at 2abe081 hash to
the same bytes: 2abe081 added the harness and touched no script. The numbers were right and the
sentence naming 2abe081 "the last one before this change" was not.
The other six are negative controls that must pass on both sides, and each is marked
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
- FR-017 / GI-003 routes and flags fire only for `user` (OT-015)
    test_operator_page_is_not_reported_for_a_route_or_a_flag
    test_operator_page_is_still_reported_for_a_symbol
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
- FR-040 / CT-004 a defect on a stub beats not-checked (OT-042)
    test_all_stub_tree_with_a_defect_exits_one

Six more tests are measured against a later commit instead, because each pins a defect one of
the widenings above introduced rather than one it fixed. Four are measured against 9859317:

- FR-010 / AC-011 the widened CODE_IDENT still excludes ALL-CAPS
    test_all_caps_with_a_digit_is_not_an_internal_symbol
- FR-010 / FR-015 / AC-011 a versioned standard name is not an internal symbol
    test_versioned_standard_names_are_not_internal_symbols
- FR-013 / CT-003 / AC-016 the screens leg carries a term a page can actually write
    test_survey_screens_carry_a_term_a_page_can_write
- FR-040 / CT-004 zero pages checked is never exit 0
    test_empty_docs_tree_is_not_checked

The first two of those four pass at cfafe8e as well — the narrow CODE_IDENT could match neither
`HTTP2` nor `IPv4` — which is what makes them regression guards rather than fix guards. The
other two are red on both sides.

The last two are measured against 19941ad, the commit before this fix. One holds three lines
that the two rounds of route widening broke in turn: 38fb601 left the file-extension exclusion
capped at the length of `.conf`, so ordinary config paths were reported as routes; lifting that
cap read `.0` as an extension, so `/api/v1.0` and `/v3.0` stopped being reported at all; and the
same commit let the path body open on a slash, so `//` became a route with no segment in it.
The other reads the printed contract rather than the rule behind it.

- FR-011 / AC-012 / OT-013 the route rule stops at a file and at a path with no segment
    test_files_are_not_routes_versions_are_and_a_bare_double_slash_is_neither
- FR-012 / FR-017 / GI-003 `types` names every class the lens reports, for the right reader
    test_types_states_the_contract_the_lens_enforces

Every test drives the script through the conftest ``run_script`` helper (GI-004, CT-007) and
every assertion quotes the captured exit code, stdout and stderr, because a bare
``assert result.returncode == 1`` tells a reader nothing about which of eight rules fired.

Two tests read ``scripts/doctype.py`` as text rather than running it, because both pin an
allowlist entry that could never match. An entry the matching regex cannot produce has no
observable behaviour to assert against in either direction: it excuses nothing and nothing is
reported, so the source is the only place the promise lives. Nothing here imports doctype
(GI-004).

No test uses ``@pytest.mark.skip`` or ``xfail``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

# The script under test, as text, for the two source-reading tests. Computed here rather than
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
    user_surface.screens[] carry `path` always and `name` only on the component-per-screen leg,
    because survey.py's add_screen() takes the name as an optional argument. Field paths and a
    function name rather than survey.py line numbers: this docstring is a claim about another
    file that nothing re-reads, and the line numbers that used to be here all pointed at the
    wrong statement."""
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

    Lifting that cap took two things with it that this pins as well. Admitting digits into the
    extension made `.0` look like one, so `/api/v1.0` and `/v3.0` stopped being reported —
    routes that the narrow /api and /v<n> rule the widening replaced had caught since the
    beginning, which made a fix into a regression. And letting the path body hold a slash made
    `` `//` `` a path with zero segments when A-008 asks for at least one.

    The plain route on the same page fires throughout, so each half pins its own exclusion
    rather than the whole rule going quiet."""
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


def test_types_states_the_contract_the_lens_enforces(run_script):
    """``doctype.py types`` is where a writer reads the lens before the lens reads them.

    It printed "may not name internal symbols, routes, environment variables and architecture"
    for a `user` page while check_lens had already grown the flag rule and widened routes past
    the /api and /v<n> prefixes. A writer read that list, wrote `--verbose`, and got a
    wrong-lens defect for a class the tool had just told them was allowed — the one failure a
    printed contract exists to prevent. The operator line is asserted from the other side: it
    must not pick the flags up, because GI-003 is what makes an operator page allowed to be
    about them."""
    result = run_script("doctype.py", "types")

    assert result.returncode == 0, f"Expected exit 0 from `types`.\n{outcome(result)}"
    lines = result.stdout.splitlines()
    blocks = {}
    for i, line in enumerate(lines):
        m = re.match(r"\s\s(user|operator|developer)\s+grade\s", line)
        if m:
            blocks[m.group(1)] = " ".join(part.strip() for part in lines[i:i + 3])
    assert set(blocks) == {"user", "operator", "developer"}, (
        f"`types` prints one three-line block per audience — the reader line, the assumes "
        f"line, and the contract. Found blocks for {sorted(blocks)}.\n{outcome(result)}"
    )
    for named in ("flag", "route"):
        assert named in blocks["user"], (
            f"The printed contract for a `user` page never says {named}, and check_lens "
            f"reports both. Got: {blocks['user']}\n{outcome(result)}"
        )
    for unnamed in ("flag", "route"):
        assert unnamed not in blocks["operator"], (
            f"GI-003 keeps {unnamed}s firing for `user` only, so the operator contract must "
            f"not claim them. Got: {blocks['operator']}\n{outcome(result)}"
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
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nOpen `AuditLog`, then run `--export` on it.\n")
    # Lowercased on both sides (CT-003): the survey says AuditLog, the page could say auditlog.
    survey = survey_json(tmp_path, screens=("AuditLog",), commands=("--export",))

    result = check(run_script, docs_dir, WEBSTER_SURVEY=str(survey))

    assert "wrong-lens" not in result.stdout, (
        f"screens.name and commands feed the same allowlist as labels.\n{outcome(result)}"
    )
    assert result.returncode == 0, f"Expected exit 0.\n{outcome(result)}"


def test_survey_screens_carry_a_term_a_page_can_write(run_script, docs_dir, tmp_path):
    """The two screen shapes survey.py really writes, neither of which is the token a page
    backticks. A component screen carries the spaced label survey.py derives from the filename
    (`DataSourcesPage.tsx` -> "Data Sources"), and a router screen carries a path and no name at
    all. Built here rather than through ``survey_json``, which always sets a name equal to the
    term under test and so could never have caught this."""
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
        f"The spaced screen name is the product naming its own screen; it can only reach the "
        f"lens with its whitespace removed.\n{outcome(result)}"
    )
    assert "an internal symbol name, `DataSourcesPage`" not in result.stdout, (
        f"A screen with no name at all still names a file, and its stem is the term a page "
        f"writes.\n{outcome(result)}"
    )
    assert result.returncode == 0, (
        f"Expected exit 0: both tokens are the product\'s own screen.\n{outcome(result)}"
    )


# -----------------------------------------------------------------------------
# FR-035 (OT-017, AC-017): the header line says what the survey contributed, and a
# survey that cannot be read is a smaller allowlist rather than a crash
# -----------------------------------------------------------------------------
def test_header_line_counts_the_terms_the_survey_contributed(run_script, docs_dir, tmp_path):
    write_page(docs_dir, "guide.md", "title: Guide\ndoc_type: how-to\naudience: user",
               "# Guide\n\nA page that names nothing forbidden.\n")
    survey = survey_json(tmp_path, labels=("DataSources", "AuditLog"), commands=("export",))

    result = check(run_script, docs_dir, WEBSTER_SURVEY=str(survey))

    assert "lens allowlist: 3 terms from WEBSTER_SURVEY" in result.stdout, (
        f"Expected the header line to name the source and the count.\n{outcome(result)}"
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
    assert "every page matches its declared type" not in result.stdout, (
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
    assert "every page matches its declared type" not in result.stdout, (
        f"There was no page to match against a declared type.\n{outcome(result)}"
    )


def test_all_stub_tree_with_a_defect_exits_one(run_script, docs_dir):
    write_stub(docs_dir, "items.md", "title: Items\ndoc_type: explanation\naudience: user")
    write_stub(docs_dir, "export.md", "title: Export\ndoc_type: how-to")

    result = check(run_script, docs_dir)

    assert result.returncode == 1, (
        f"A frontmatter defect on a stub is a real finding and wins over not-checked, the way "
        f"a broken anchor wins in drift.py (FR-040).\n{outcome(result)}"
    )
    assert "no-audience" in result.stdout, (
        f"Expected the no-audience defect to be reported, not swallowed by the exit 2 path."
        f"\n{outcome(result)}"
    )
    assert "nothing to check" not in result.stdout, (
        f"The nothing-to-check line prints only when no defect was found (FR-036)."
        f"\n{outcome(result)}"
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
    assert "1 pages checked" in result.stdout, (
        f"Expected the header to count the one page that was read as writing."
        f"\n{outcome(result)}"
    )
