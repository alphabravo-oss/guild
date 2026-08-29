"""Tests for plugins/webster/scripts/survey.py (spec US-006).

One of the test modules US-010 asks for, one per script.

Which spec fix each test pins, and the revision it is RED against (FR-039,
AC-039). Measured, not reasoned about. Each row is one run: copy the plugin to
a scratch directory, put that revision's survey.py in it, and run this module
against it --

    cp -R plugins/webster "$scratch/webster"
    git show REV:plugins/webster/scripts/survey.py \
        > "$scratch/webster/scripts/survey.py"
    cd "$scratch/webster" && uvx pytest tests/test_survey.py -v

This table is the record, and it is the only one. An earlier version of it
cited a committed evidence log for the run; the run's evidence is stripped from
the repository once consumed, so the citation named a file no reader can open
and the table was left asserting a measurement nobody could reach. The command
above is here instead, because a record whose backing nobody can re-run is a
record that reports a pass without checking -- and an earlier version of this
table, written by reading the diffs rather than by running anything, did get a
row in the sibling module wrong. See the correction note in ``test_llmstxt.py``.

"red against REV" means this file fails when the suite runs against the
survey.py at REV. "guard" means it passed at every revision measured and exists
to stop a fix from taking something else away with it. cfafe8e is the
pre-change script AC-039 names; a test added by a later fix names the commit it
was written against.

============================================================  ==========  =======================
test                                                          fix         red against
============================================================  ==========  =======================
test_os_getenv_read_lands_in_config_undeclared                FR-018/031  cfafe8e
test_env_regex_allows_whitespace_after_the_bracket            FR-018/031  cfafe8e
test_env_regex_still_matches_the_go_getenv                    FR-031      guard
test_black_split_decorator_is_anchored_to_the_at_line         FR-019      cfafe8e
test_mock_patch_decorator_is_not_an_http_endpoint             FR-019      a802345 (added 4ae7334)
test_flask_route_reports_its_declared_methods                 FR-019      cfafe8e
test_flask_route_without_methods_reports_get                  FR-019      cfafe8e
test_pyproject_name_description_and_scripts                   FR-020/021  cfafe8e
test_poetry_legacy_tables_are_the_fallback                    FR-020/021  cfafe8e
test_package_json_still_wins_over_pyproject                   FR-020      guard
test_unparseable_pyproject_leaves_the_survey_running          FR-020      guard
test_httpexception_detail_and_positional_are_messages         FR-022/032  cfafe8e
test_httpexception_dict_detail_contributes_no_message         FR-022      guard
test_httpexception_publishes_only_its_own_arguments           FR-022/032  19941ad (added cycle 4)
test_message_floors_are_measured_in_whole_strings             FR-022      cfafe8e (added cycle 3)
test_detail_after_a_collection_argument_is_still_a_message    FR-022/032  944c958 (added cycle 5)
test_option_flags_reach_the_user_surface_commands             FR-012      19941ad (added cycle 4)
test_a_flag_named_only_in_prose_is_not_a_command              FR-012      19941ad (added cycle 4)
test_a_flag_named_in_another_flags_help_is_not_a_command      FR-012      944c958 (added cycle 5)
test_a_positional_description_is_not_a_flag_spec              FR-012      66dab76 (added cycle 6)
test_a_commander_option_string_still_declares_its_spellings   FR-012      guard
test_a_black_split_declaration_still_declares_its_flag        FR-012      66dab76 (added cycle 6)
test_tomllib_imported_at_module_level_with_the_floor_comment  NFR-002     cfafe8e
============================================================  ==========  =======================

Rows carrying a note the column has no room for:

- ``test_mock_patch_decorator_is_not_an_http_endpoint`` is red at cfafe8e too.
  It names a802345 because a802345 is the commit whose decorator joining it was
  written against, and the nearer baseline is the informative one.
- ``test_message_floors_are_measured_in_whole_strings`` was added in cycle 3 to
  measure the two message floors rather than restate them in a comment, and is
  red at cfafe8e because the HTTPException branch it measures did not exist
  there. Against a802345 and every commit since it is a guard.
- The three rows naming 19941ad are red at cfafe8e and at every revision
  between, measured. Each names the nearest baseline for the same reason.
- ``test_a_flag_named_only_in_prose_is_not_a_command`` is red at 19941ad for a
  reason the column cannot show: there the commands list for its tree was empty
  altogether, so it fails because nothing was emitted rather than because noise
  was. It is a boundary on the fix beside it -- every name in that list is a
  term doctype.py stops reporting -- and from this cycle on it is a guard.
- The two rows naming 944c958 are the cycle-5 corrections, and each is red at
  944c958 for one assertion out of its several. In
  ``test_a_flag_named_in_another_flags_help_is_not_a_command`` it is the
  ``--keep`` declaration, whose help sentence opens with a flag; the ``--out``
  declaration the defect names is already green there, and the test docstring
  says so rather than leaving the reader to assume otherwise. In
  ``test_detail_after_a_collection_argument_is_still_a_message`` it is the three
  detail= strings; the three exclusions in the same tree are green at 944c958
  and stay green, which is what makes them the control.
- The two rows naming 66dab76 are the cycle-6 corrections to the same flag
  scan, and each is red there for the whole of what it measures rather than for
  one assertion: at 66dab76 the commander tree publishes ``-x`` and ``--purge``
  along with ``--keep``, and the Black-split tree publishes nothing at all.
  ``test_a_commander_option_string_still_declares_its_spellings`` between them
  is the guard on the first fix -- an option string holding two spellings and a
  ``<value>`` placeholder is a declaration, and stopping the scan at prose must
  not stop it there.

Every test drives the script through the conftest ``run_script`` helper. Nothing
here imports survey.py: its module-level ``ROOT`` resolves from ``sys.argv`` at
import time, so an import would freeze the wrong root before a test could set
one (GI-004). Named rather than numbered, for the reason conftest.py's docstring
sets out at greater length: the line numbers this suite's first draft cited into
the scripts had every one of them moved by the time anybody read them.

No test uses ``@pytest.mark.skip`` or ``xfail``.
"""

from __future__ import annotations

import json
from pathlib import Path

# Read as text, never imported — see the module docstring. survey.py lives beside
# the tests directory, so the path is derived rather than hardcoded.
SURVEY_SOURCE = Path(__file__).resolve().parent.parent / "scripts" / "survey.py"

# A local reader, not a fixture: it calls the one ``run_script`` helper rather
# than re-implementing subprocess, which is the thing GI-004 forbids copying.
# Its job is to fail loudly with both streams attached before a test starts
# indexing into JSON that was never produced.
def survey(run_script, root):
    result = run_script("survey.py", root)
    assert result.returncode == 0, (
        f"Expected survey.py to exit 0 on {root}; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - only on a broken run
        raise AssertionError(
            f"survey.py did not print JSON ({exc})\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        ) from exc


def python_project(tmp_path, name, source, pyproject=None, package_json=None):
    """A minimal repo root survey.py will treat as Python.

    survey.py runs its FastAPI/Flask decorator pass under an ``if "python" in
    stack:`` guard -- the one immediately below the ``PY_DECORATOR`` and
    ``FLASK_METHODS`` patterns, not the two other guards spelled the same way --
    and the stack marker is a pyproject.toml on disk, so a tree holding only a
    .py file is surveyed as having no HTTP surface at all.

    Named rather than numbered on purpose. This sentence carried a line number
    until the line moved, and by then the number resolved to the Next.js
    pages-api branch instead -- a citation that lands on the wrong code is worse
    than no citation at all, because it reads as having been checked.
    """
    root = tmp_path / name
    (root / "src").mkdir(parents=True)
    (root / "src" / "api.py").write_text(source, encoding="utf-8")
    root.joinpath("pyproject.toml").write_text(
        pyproject if pyproject is not None else '[project]\nname = "demo"\n',
        encoding="utf-8",
    )
    if package_json is not None:
        root.joinpath("package.json").write_text(package_json, encoding="utf-8")
    return root


def config_named(data, name):
    return [c for c in data["surface"]["config"] if c["name"] == name]


def message_texts(data):
    return [m["text"] for m in data["user_surface"]["messages"]]


# -----------------------------------------------------------------------------
# FR-018 / FR-031: os.getenv joins the env alternation, which is written once
# -----------------------------------------------------------------------------
def test_os_getenv_read_lands_in_config_undeclared(run_script, fixture_repo):
    """AC-022, OT-021: APP_DEBUG is read only through os.getenv in the fixture."""
    data = survey(run_script, fixture_repo)

    names = {c["name"] for c in data["surface"]["config"]}
    assert names >= {"APP_DEBUG", "DATABASE_URL", "REQUEST_TIMEOUT"}, (
        "Expected the os.getenv read alongside the os.environ ones; "
        f"surface.config held {sorted(names)}"
    )
    app_debug = config_named(data, "APP_DEBUG")
    assert app_debug and app_debug[0].get("undeclared") is True, (
        "APP_DEBUG is read by the code and declared in no .env.example, which is "
        f"the whole finding; got {app_debug}"
    )
    assert app_debug[0]["anchor"] == "src/app/main.py:8", (
        f"Expected the anchor on the os.getenv line; got {app_debug[0]['anchor']}"
    )


def test_env_regex_allows_whitespace_after_the_bracket(run_script, tmp_path):
    """FR-018: a formatter may put a space after ``(`` or ``[``."""
    root = python_project(
        tmp_path,
        "spaced",
        "import os\n"
        '\nPADDED = os.environ.get( "PADDED_VAR" )\n'
        '\nBRACKET = os.environ[ "BRACKET_VAR" ]\n'
        '\nSPACED = os.getenv( "SPACED_VAR" )\n'
        '\nPLAIN = os.getenv("PLAIN_VAR", "x")\n',
    )

    data = survey(run_script, root)

    names = {c["name"] for c in data["surface"]["config"]}
    assert names == {"PADDED_VAR", "BRACKET_VAR", "SPACED_VAR", "PLAIN_VAR"}, (
        f"Every spelling above is one variable this repo reads; got {sorted(names)}"
    )


def test_env_regex_still_matches_the_go_getenv(run_script, tmp_path):
    """FR-031: adding Python's os.getenv must not displace Go's os.Getenv."""
    root = tmp_path / "goapp"
    root.mkdir()
    root.joinpath("go.mod").write_text("module example.com/x\n", encoding="utf-8")
    root.joinpath("main.go").write_text(
        'package main\n\nfunc main() {\n\t_ = os.Getenv("GO_VAR")\n}\n', encoding="utf-8"
    )

    data = survey(run_script, root)

    assert config_named(data, "GO_VAR"), (
        "The Go branch is a separate spelling and stays; "
        f"surface.config held {data['surface']['config']}"
    )


# -----------------------------------------------------------------------------
# FR-019: decorators split across lines, and Flask's real verbs
# -----------------------------------------------------------------------------
def test_black_split_decorator_is_anchored_to_the_at_line(run_script, tmp_path):
    """AC-023, OT-022: the shape Black writes for a two-kwarg decorator."""
    # Line 1 is the import, so the @ is on line 4 and the path on line 5. The
    # anchor has to name the first of those.
    root = python_project(
        tmp_path,
        "black",
        "from fastapi import FastAPI\n"  # 1
        "\n"  # 2
        "app = FastAPI()\n"  # 3
        "@app.post(\n"  # 4
        '    "/items/{item_id}",\n'  # 5
        "    response_model=Item,\n"  # 6
        ")\n"  # 7
        "def create_item(item_id: int):\n"  # 8
        "    return {}\n",  # 9
    )

    data = survey(run_script, root)

    posts = [h for h in data["surface"]["http"] if h["method"] == "POST"]
    assert posts, (
        "A decorator whose path sits on its own line is still a route; "
        f"surface.http held {data['surface']['http']}"
    )
    assert posts[0]["path"] == "/items/{item_id}", f"got {posts[0]}"
    assert posts[0]["anchor"] == "src/api.py:4", (
        "The anchor is the @ line, which is what a reader is sent to read; "
        f"got {posts[0]['anchor']}"
    )


def test_mock_patch_decorator_is_not_an_http_endpoint(run_script, tmp_path):
    """FR-019, AC-023: unittest.mock spells its decorator ``patch``, like the verb.

    ``@\\w+\\.`` matches any object, so ``@mock.patch(`` reads as an HTTP PATCH on
    a path of ``pkg.mod.func``. Joining continuation lines made it worse rather
    than better: a Black-split mock that used to escape the single-line scan
    entirely now gets accumulated and published. An endpoint no server serves is
    worse than a missing one, because a writer is told to document it.

    The Black-split ``@app.post(`` route in the same tree is the control: the
    join has to keep finding AC-023's real route while it stops finding this.
    """
    root = python_project(
        tmp_path,
        "mocks",
        "from fastapi import FastAPI\n"  # 1
        "\n"  # 2
        "app = FastAPI()\n"  # 3
        "@app.post(\n"  # 4
        '    "/items/{item_id}",\n'  # 5
        "    response_model=Item,\n"  # 6
        ")\n"  # 7
        "def create_item(item_id: int):\n"  # 8
        "    return {}\n",  # 9
    )
    (root / "tests").mkdir()
    (root / "tests" / "test_api.py").write_text(
        "from unittest import mock\n"
        "\n\n@mock.patch(\n"
        '    "pkg.mod.func",\n'
        "    return_value=1,\n"
        ")\n"
        "def test_thing(patched):\n"
        "    assert patched\n"
        '\n\n@mock.patch("pkg.other.func")\n'
        "def test_single_line(patched):\n"
        "    assert patched\n",
        encoding="utf-8",
    )

    data = survey(run_script, root)

    hits = data["surface"]["http"]
    assert [h for h in hits if h["anchor"].startswith("tests/")] == [], (
        "A mocked import path is not a route, and the file it sits in makes no "
        f"difference to that; surface.http held {hits}"
    )
    assert [h for h in hits if not h["path"].startswith("/")] == [], (
        "Every HTTP path a client can send starts with a slash; anything else "
        f"came from a decorator that only looks like a route; got {hits}"
    )
    assert [(h["method"], h["path"], h["anchor"]) for h in hits] == [
        ("POST", "/items/{item_id}", "src/api.py:4")
    ], f"the Black-split route AC-023 asks for is still the whole surface; got {hits}"


def test_flask_route_reports_its_declared_methods(run_script, tmp_path):
    """AC-024, OT-023: ``methods=["POST"]`` is a POST, not the word ROUTE."""
    root = python_project(
        tmp_path,
        "flaskverbs",
        "from flask import Flask\n"
        "\napp = Flask(__name__)\n"
        '\n\n@app.route("/users", methods=["POST"])\n'
        "def users():\n"
        "    return {}\n"
        '\n\n@app.route("/things", methods=["GET", "PUT"])\n'
        "def things():\n"
        "    return {}\n",
    )

    data = survey(run_script, root)

    by_path = {}
    for hit in data["surface"]["http"]:
        by_path.setdefault(hit["path"], set()).add(hit["method"])
    assert by_path.get("/users") == {"POST"}, (
        "ROUTE is a verb no client can send, so the docs written from it were "
        f"wrong in the one detail a reader copies; got {data['surface']['http']}"
    )
    assert by_path.get("/things") == {"GET", "PUT"}, (
        f"A methods list of two declares two routes; got {data['surface']['http']}"
    )


def test_flask_route_without_methods_reports_get(run_script, tmp_path):
    """OT-023: Flask's default with no methods kwarg is GET."""
    root = python_project(
        tmp_path,
        "flaskdefault",
        "from flask import Flask\n"
        "\napp = Flask(__name__)\n"
        '\n\n@app.route("/health")\n'
        "def health():\n"
        "    return {}\n",
    )

    data = survey(run_script, root)

    assert [(h["method"], h["path"]) for h in data["surface"]["http"]] == [
        ("GET", "/health")
    ], f"got {data['surface']['http']}"


# -----------------------------------------------------------------------------
# FR-020 / FR-021: pyproject.toml is where a Python project keeps its name
# -----------------------------------------------------------------------------
def test_pyproject_name_description_and_scripts(run_script, fixture_repo):
    """AC-025, OT-024, CT-008: the fixture has a pyproject.toml and no package.json."""
    assert not (fixture_repo / "package.json").exists(), (
        "This test is about the no-package.json path; the fixture grew one"
    )

    data = survey(run_script, fixture_repo)

    assert data["name"] == "fixapp", (
        f"Expected the [project] name, not the checkout directory; got {data['name']!r}"
    )
    assert data["description"] == "A small item store served over HTTP.", (
        f"got {data['description']!r}"
    )
    assert {"name": "fixapp", "target": "cli.main:main", "anchor": "pyproject.toml:1"} in (
        data["surface"]["cli"]
    ), (
        "A [project.scripts] entry is a command somebody types, in the same shape "
        f"as a package.json bin entry; surface.cli held {data['surface']['cli']}"
    )


def test_poetry_legacy_tables_are_the_fallback(run_script, tmp_path):
    """FR-020, FR-021: [tool.poetry] for files written before Poetry 2.0."""
    root = python_project(
        tmp_path,
        "poetry",
        "x = 1\n",
        pyproject=(
            "[tool.poetry]\n"
            'name = "poetryapp"\n'
            'description = "Legacy Poetry layout."\n'
            "\n[tool.poetry.scripts]\n"
            'oldcmd = "pkg.mod:run"\n'
        ),
    )

    data = survey(run_script, root)

    assert data["name"] == "poetryapp", f"got {data['name']!r}"
    assert data["description"] == "Legacy Poetry layout.", f"got {data['description']!r}"
    assert [c["name"] for c in data["surface"]["cli"]] == ["oldcmd"], (
        f"got {data['surface']['cli']}"
    )


def test_package_json_still_wins_over_pyproject(run_script, tmp_path):
    """FR-020: a repo with a package.json is a Node project that also has tooling config."""
    root = python_project(
        tmp_path,
        "both",
        "x = 1\n",
        pyproject='[project]\nname = "pyapp"\ndescription = "From pyproject."\n',
        package_json='{"name": "nodeapp", "description": "From package.json."}',
    )

    data = survey(run_script, root)

    assert (data["name"], data["description"]) == ("nodeapp", "From package.json."), (
        f"got {data['name']!r}, {data['description']!r}"
    )


def test_unparseable_pyproject_leaves_the_survey_running(run_script, fixture_repo):
    """AC-027, OT-026: a pyproject.toml is a syntax error while somebody edits it."""
    (fixture_repo / "pyproject.toml").write_text(
        '[project]\nname = \ndescription = "never read"\n', encoding="utf-8"
    )

    result = run_script("survey.py", fixture_repo)

    assert result.returncode == 0, (
        "A broken pyproject.toml must cost the writer that file's contents, not "
        f"the whole surface; got {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    data = json.loads(result.stdout)
    assert data["name"] == fixture_repo.name, (
        f"Expected the directory-name fallback; got {data['name']!r}"
    )
    assert data["surface"]["http"], (
        "The rest of the survey still ran; surface.http was empty"
    )


# -----------------------------------------------------------------------------
# FR-022 / FR-032: HTTPException is what a FastAPI reader actually meets
# -----------------------------------------------------------------------------
def test_httpexception_detail_and_positional_are_messages(run_script, tmp_path):
    """AC-026, OT-025: both spellings of a FastAPI error carry a real message."""
    root = python_project(
        tmp_path,
        "messages",
        "from fastapi import FastAPI, HTTPException\n"
        "\napp = FastAPI()\n"
        "\n\ndef one():\n"
        '    raise HTTPException(status_code=404, detail="Item not found")\n'
        "\n\ndef two():\n"
        '    raise HTTPException(404, "Not found")\n',
    )

    data = survey(run_script, root)

    texts = message_texts(data)
    assert "Item not found" in texts, f"detail= keyword form missing; got {texts}"
    assert "Not found" in texts, (
        f"positional form missing, and nine characters is a whole 404; got {texts}"
    )


def test_httpexception_dict_detail_contributes_no_message(run_script, tmp_path):
    """OT-025: only a string literal is text somebody reads."""
    root = python_project(
        tmp_path,
        "dictdetail",
        "from fastapi import FastAPI, HTTPException\n"
        "\napp = FastAPI()\n"
        "\n\ndef one():\n"
        '    raise HTTPException(status_code=422, detail={"loc": "Body of the request"})\n',
    )

    data = survey(run_script, root)

    assert message_texts(data) == [], (
        "A dict detail is a machine-readable payload, not a sentence; "
        f"got {message_texts(data)}"
    )


def test_detail_after_a_collection_argument_is_still_a_message(run_script, tmp_path):
    """FR-022, FR-032, AC-026: which mechanism keeps a dict detail out, measured.

    The comment above the pattern used to say the gap before the literal
    excluded `{` and `[` because a collection detail is a machine-readable
    payload. It is the quote required immediately after ``detail=`` that does
    that, and the exclusions could only ever reach something else: a real
    ``detail=`` string standing after an earlier argument that happened to be a
    collection or a string. ``headers={"X": "1"}`` ahead of the detail cost the
    writer the sentence the product actually shows, which is the failure the
    branch exists to fix rather than an instance of it.

    The dict and list details in the same tree are the control -- what excludes
    them has to keep excluding them once the gap stops trying to.
    """
    root = python_project(
        tmp_path,
        "afterdict",
        "from fastapi import FastAPI, HTTPException\n"
        "\napp = FastAPI()\n"
        "\n\ndef after_dict():\n"
        '    raise HTTPException(status_code=422, headers={"X": "1"}, detail="Bad input")\n'
        "\n\ndef after_list():\n"
        '    raise HTTPException(status_code=422, allow=[1, 2], detail="Too many")\n'
        "\n\ndef after_string():\n"
        '    raise HTTPException(status_code=400, code="bad_req", detail="Try again")\n'
        "\n\ndef dict_detail():\n"
        '    raise HTTPException(status_code=422, detail={"loc": "Body of the request"})\n'
        "\n\ndef list_detail():\n"
        '    raise HTTPException(status_code=422, detail=[{"msg": "Field is required"}])\n'
        "\n\ndef mapped_detail():\n"
        '    raise HTTPException(status_code=404, detail=CODES["Item not found"])\n',
    )

    texts = message_texts(survey(run_script, root))

    assert "Bad input" in texts, (
        "A detail= string is the sentence the product shows, and an earlier "
        f"argument being a dict does not make it less so; got {texts}"
    )
    assert "Too many" in texts, f"the same for a list argument; got {texts}"
    assert "Try again" in texts, f"and for a string argument; got {texts}"
    assert "Body of the request" not in texts, (
        "A dict detail is kept out by the quote demanded after detail=, which a "
        f"`{{` fails on its first character; got {texts}"
    )
    assert "Field is required" not in texts, f"a list detail the same way; got {texts}"
    assert "Item not found" not in texts, (
        "and a detail read out of a lookup table is not a literal this file "
        f"holds either; got {texts}"
    )


def test_httpexception_publishes_only_its_own_arguments(run_script, tmp_path):
    """FR-022, FR-032, AC-026: a message the product never says is worse than one missed.

    The branch used to reach its string through a plain 40-character window
    opening at ``HTTPException(``, and a window that wide runs straight past the
    closing paren. A source comment on the same line, and the other arm of a
    ternary, were both published as text this product shows a user. Neither is a
    string it emits at all, so a troubleshooting page written from the survey
    was written against something that cannot happen.

    The two documented forms in the same tree are the control: bounding the scan
    to the argument list has to keep finding both.
    """
    root = python_project(
        tmp_path,
        "argsonly",
        "from fastapi import FastAPI, HTTPException\n"
        "\n\ndef commented():\n"
        '    raise HTTPException(status_code=410)  # "Gone for good now"\n'
        "\n\ndef either(missing):\n"
        '    return HTTPException(404) if missing else fallback("Item was archived")\n'
        "\n\ndef keyword():\n"
        '    raise HTTPException(status_code=404, detail="Item not found")\n'
        "\n\ndef positional():\n"
        '    raise HTTPException(404, "Not found")\n',
    )

    texts = message_texts(survey(run_script, root))

    assert "Gone for good now" not in texts, (
        "A comment is a note to the next reader of the source, not a string the "
        f"product ever prints; got {texts}"
    )
    assert "Item was archived" not in texts, (
        "The else arm of a ternary is nowhere inside the HTTPException call, and "
        f"standing within forty characters of one is not being an argument; got {texts}"
    )
    assert "Item not found" in texts, f"the detail= form is still a message; got {texts}"
    assert "Not found" in texts, f"the positional form is still a message; got {texts}"


def test_message_floors_are_measured_in_whole_strings(run_script, tmp_path):
    """FR-022, AC-026: both branches' floors, counted the one way that is visible.

    The comment above the two patterns gave one floor in characters and the
    other in quantifier digits -- "four characters ... rather than the twelve
    above" -- so the two numbers read as a comparison and were not one:
    ``[A-Z][^"\']{12,110}`` takes thirteen characters, not twelve. Restating a
    regex in prose is how that happens, so the numbers are measured here
    instead. Thirteen and twelve for the messages branch, four and three for
    the HTTPException branch, each side of its own floor.
    """
    root = python_project(
        tmp_path,
        "floors",
        "from fastapi import HTTPException\n"
        "\n\ndef long_enough():\n"
        '    raise ValueError("Login failed.")\n'  # 13 characters
        "\n\ndef one_short():\n"
        '    raise ValueError("Login failed")\n'  # 12
        "\n\ndef http_long_enough():\n"
        '    raise HTTPException(410, "Gone")\n'  # 4
        "\n\ndef http_one_short():\n"
        '    raise HTTPException(400, "Bad")\n',  # 3
    )

    texts = message_texts(survey(run_script, root))

    assert "Login failed." in texts, (
        f"thirteen characters clears the messages floor; got {texts}"
    )
    assert "Login failed" not in texts, (
        "twelve does not -- the same sentence without its full stop is the "
        f"whole difference, and it is the floor the comment names; got {texts}"
    )
    assert "Gone" in texts, (
        "four characters clears the HTTPException floor, which is what A-018's "
        f"{{3,110}} comes to with the capital counted; got {texts}"
    )
    assert "Bad" not in texts, f"three does not; got {texts}"


# -----------------------------------------------------------------------------
# FR-012 / AC-013 / OT-014: the flags a product declares are flags it may document
# -----------------------------------------------------------------------------
def test_option_flags_reach_the_user_surface_commands(run_script, fixture_repo):
    """FR-012, AC-013, OT-014: the list doctype.py's flag lens is suppressed by.

    doctype.py reports a backticked ``--verbose`` on a user page and excuses it
    when the product's own survey names it in ``user_surface.commands``. A
    command name has to begin with a lowercase letter, so the survey could not
    emit a hyphen-led token at all and the excuse had nothing to draw on: the
    whole WEBSTER_SURVEY path for flags was inert end to end, and every flag a
    command-line product documented stayed a defect until somebody named it by
    hand in WEBSTER_LENS_ALLOW.

    The fixture's CLI declares three subcommands and one option, which is both
    halves of the check: the option has to arrive, and the subcommands the
    pattern already found have to still be there.
    """
    source = (fixture_repo / "src" / "cli" / "main.py").read_text(encoding="utf-8")
    assert 'add_argument("--out")' in source, (
        "This test is about that declaration; the fixture CLI no longer carries it"
    )

    data = survey(run_script, fixture_repo)

    commands = data["user_surface"]["commands"]
    names = {c["name"] for c in commands}
    assert "--out" in names, (
        "A flag the product declares is a flag its user pages are allowed to "
        f"name, and this is the only list that says so; commands held {commands}"
    )
    assert {"serve", "migrate", "export-data"} <= names, (
        f"the subcommands the pattern already found are still there; got {commands}"
    )
    out = [c for c in commands if c["name"] == "--out"]
    assert out[0]["anchor"] == "src/cli/main.py:10", (
        "The anchor is the declaration a reader can be sent to read, which is "
        f"what makes the entry checkable rather than a claim; got {out}"
    )


def test_a_flag_named_only_in_prose_is_not_a_command(run_script, tmp_path):
    """FR-012, GI-002: this list widens a check, so what goes in it is load-bearing.

    Every name here becomes a term doctype.py stops reporting. A dash inside a
    help string, or a default value of ``"-"``, would therefore turn a real
    finding into a pass, and a false pass is worse than a finding because the
    reader trusts the page exactly as far as they trust the gate. What is read
    is the call's option strings, which are its positional arguments; a
    keyword's value is not one. ``test_a_flag_named_in_another_flags_help_is_not_a_command``
    below is the case that names why the distinction has to be positional rather
    than a guess about what a literal looks like.
    """
    root = python_project(
        tmp_path,
        "flagnoise",
        "import argparse\n"
        "\n\ndef build():\n"
        "    parser = argparse.ArgumentParser()\n"
        '    parser.add_argument("outfile")\n'
        '    parser.add_argument("-v", "--verbose", help="pass -x for the old behaviour")\n'
        '    parser.add_argument("--retries", type=int, default="-")\n'
        "    return parser\n",
    )

    names = {c["name"] for c in survey(run_script, root)["user_surface"]["commands"]}

    assert names == {"-v", "--verbose", "--retries"}, (
        "One literal per flag spec: the positional is not a flag, the -x in a "
        f"help sentence is prose, and a default of - is a value; got {sorted(names)}"
    )


def test_a_flag_named_in_another_flags_help_is_not_a_command(run_script, tmp_path):
    """FR-012, AC-013, GI-002: a flag the product does not have, excusing a page that names it.

    The test beside this one holds because ``help="pass -x for the old
    behaviour"`` does not begin with a dash, not because the branch knew it was
    a help string: every literal on the line was read and the dash-leading ones
    were mined for flags. A help sentence that opens with a flag is ordinary
    English -- ``help="-x is the short form of --purge"`` -- and it declared -x
    and --purge as commands this product has.

    That is a false pass with a name on it. doctype.py folds
    ``user_surface.commands`` into the allowlist its flag lens is suppressed by,
    so a page that backticks ``--purge`` stops being reported: the wrong-lens
    finding is waived by the survey's own claim that the product has a --purge,
    and the product does not. Reading only the positional option strings is what
    makes the excuse come from a declaration rather than from prose about one.

    ``--out``'s own help sentence names ``--force`` mid-string, which the old
    branch already let alone -- measured, at 944c958, before this was written.
    It is here because it is the shape the defect describes, and it is a
    boundary rather than the thing that goes red: the ``--keep`` line beside it
    is what fails without the fix.
    """
    root = python_project(
        tmp_path,
        "helpprose",
        "import argparse\n"
        "\n\ndef build():\n"
        "    parser = argparse.ArgumentParser()\n"
        '    parser.add_argument("--out", help="unlike --force, writes to a path")\n'
        '    parser.add_argument("--keep", help="-x is the short form of --purge")\n'
        '    parser.add_argument("-n", "--dry-run", action="store_true", default="-")\n'
        "    return parser\n",
    )

    names = {c["name"] for c in survey(run_script, root)["user_surface"]["commands"]}

    assert "--out" in names, (
        f"the flag this call declares is still the flag it declares; got {sorted(names)}"
    )
    assert "--force" not in names, (
        "--force appears in this repo only inside another flag's help text, so a "
        "page naming it would be excused by a command that does not exist; got "
        f"{sorted(names)}"
    )
    assert names == {"--out", "--keep", "-n", "--dry-run"}, (
        "-x and --purge are prose in a help sentence and store_true and - are "
        f"keyword values, so none of the four is declared here; got {sorted(names)}"
    )


def test_a_positional_description_is_not_a_flag_spec(run_script, tmp_path):
    """FR-012, AC-013, GI-002: the description is a positional argument too.

    The test above holds because argparse and click spell a flag's prose as
    ``help=``, and the scan stops at the first keyword argument. commander and
    yargs do not spell it that way: ``.option(flags, description)`` puts the
    sentence in the second *positional* argument, so no ``name=`` ends the scan
    and the closing paren is the only stop. Every literal in the call was read,
    and ``.option("--keep", "-x is the short form of --purge")`` published
    ``--keep``, ``-x`` and ``--purge`` alike.

    That is the same false pass as the one beside it, arriving by the other
    door. doctype.py folds ``user_surface.commands`` into the allowlist its flag
    lens is suppressed by, so a user page backticking ``--purge`` passed --
    excused by the survey's own claim that this product has a ``--purge``, and
    it has no such flag.

    What ends the positional run is therefore the shape of the literal rather
    than the syntax around it: a flag spec is option spellings and nothing else,
    and the first literal that is not one is prose.
    """
    root = python_project(tmp_path, "commanderprose", "# no CLI in this file\n")
    root.joinpath("src", "cli.js").write_text(
        'program.option("--keep", "-x is the short form of --purge");\n',
        encoding="utf-8",
    )

    names = {c["name"] for c in survey(run_script, root)["user_surface"]["commands"]}

    assert "--keep" in names, (
        "the flag this call declares is still the flag it declares; got "
        f"{sorted(names)}"
    )
    assert names == {"--keep"}, (
        "-x and --purge are prose in the description argument, and every name "
        "in this list is a term doctype.py stops reporting on a user page; got "
        f"{sorted(names)}"
    )


def test_a_commander_option_string_still_declares_its_spellings(run_script, tmp_path):
    """FR-012: one literal, both spellings, and the value the flag takes.

    commander writes the spellings and the option's own argument into a single
    string -- ``"-o, --out <path>"`` -- so the tokens inside one literal are
    read individually, and ``<path>`` names a value rather than a flag. Ending
    the positional run at the first literal that is not a pure flag spec must
    not end it here: this literal *is* a declaration, and both flags in it are
    flags the product has.

    This is the boundary on the test above rather than the thing that goes red.
    It is green at 66dab76 and stays green, which is what makes it the control:
    the placeholder was already dropped by the token filter there, and the fix
    beside it must not take the two spellings with it.
    """
    root = python_project(tmp_path, "commanderspec", "# no CLI in this file\n")
    root.joinpath("src", "cli.js").write_text(
        "program\n"
        '  .option("-k, --keep <n>", "how many builds to keep")\n'
        '  .option("-o, --out [dir]", "where to write")\n',
        encoding="utf-8",
    )

    names = {c["name"] for c in survey(run_script, root)["user_surface"]["commands"]}

    assert names == {"-k", "--keep", "-o", "--out"}, (
        "Both spellings out of each option string, and neither placeholder: "
        f"<n> and [dir] name a value the flag takes, not a flag; got {sorted(names)}"
    )


def test_a_black_split_declaration_still_declares_its_flag(run_script, tmp_path):
    """FR-012, AC-013, CT-003: one line per declaration is a formatter's choice.

    Black puts every argument on its own line as soon as the call passes 88
    columns, which is the ordinary shape of an ``add_argument`` carrying an
    action and a help sentence -- the same formatter behaviour the decorator
    pass was already taught to read. A scan reading one line at a time saw
    ``parser.add_argument(`` and no literal at all, so a formatted project
    declared no flags whatsoever and doctype.py had nothing to suppress a
    ``--verbose`` on a user page with: the WEBSTER_SURVEY path was inert for
    exactly the projects that run a formatter.

    The join is the one the decorator pass already uses, cap and all, and the
    anchor stays the line the call opens on because that is the line a reader is
    sent to read. Joining does not relax what is read from the call: ``-x`` in
    the help sentence is still prose, and so is the description commander splits
    across lines the same way.
    """
    root = python_project(
        tmp_path,
        "blacksplit",
        "import argparse\n"
        "\n\ndef build():\n"
        "    parser = argparse.ArgumentParser()\n"
        "    parser.add_argument(\n"
        '        "--verbose",\n'
        '        action="store_true",\n'
        '        help="pass -x for the old behaviour",\n'
        "    )\n"
        "    return parser\n",
    )
    root.joinpath("src", "cli.js").write_text(
        "program\n"
        "  .option(\n"
        '    "--keep-going",\n'
        '    "-x is the short form of --purge",\n'
        "  );\n",
        encoding="utf-8",
    )

    commands = survey(run_script, root)["user_surface"]["commands"]
    names = {c["name"] for c in commands}

    assert names == {"--verbose", "--keep-going"}, (
        "Both declarations are split across lines, and neither flag named "
        f"inside a description is one this product has; got {commands}"
    )
    anchors = {c["name"]: c["anchor"] for c in commands}
    assert anchors["--verbose"] == "src/api.py:6", (
        "The anchor is the line the call opens on, not the line the spelling "
        f"happened to land on: that is the line a reader is sent to; got {anchors}"
    )
    assert anchors["--keep-going"] == "src/cli.js:2", (
        f"same for the .option( a formatter split across lines; got {anchors}"
    )


# -----------------------------------------------------------------------------
# NFR-002 / OT-041: the Python floor is stated where the dependency is taken
# -----------------------------------------------------------------------------
def test_tomllib_imported_at_module_level_with_the_floor_comment():
    """NFR-002, OT-041: tomllib is 3.11+, and the floor is written down."""
    source = SURVEY_SOURCE.read_text(encoding="utf-8")
    head = source.split("ROOT =")[0]

    assert "import tomllib" in head, (
        "tomllib is imported at module level, not inside the function that needs it; "
        f"the header of {SURVEY_SOURCE} was:\n{head}"
    )
    assert "3.11" in head, (
        f"A version floor nobody wrote down is a floor nobody knows about:\n{head}"
    )
