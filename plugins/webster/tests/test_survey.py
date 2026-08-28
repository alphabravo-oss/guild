"""Tests for plugins/webster/scripts/survey.py (spec US-006).

One of the test modules US-010 asks for, one per script.

Which spec fix each test pins, and whether it was RED against the pre-change
script (FR-039, AC-039). "Red" means the test fails when run against the
survey.py at the commit before this one; "guard" means it passed there too and
exists to stop the fix from taking something else away with it.

===========================================================  ==========  =======
test                                                          fix         state
===========================================================  ==========  =======
test_os_getenv_read_lands_in_config_undeclared                FR-018/031  red
test_env_regex_allows_whitespace_after_the_bracket            FR-018/031  red
test_env_regex_still_matches_the_go_getenv                    FR-031      guard
test_black_split_decorator_is_anchored_to_the_at_line         FR-019      red
test_flask_route_reports_its_declared_methods                 FR-019      red
test_flask_route_without_methods_reports_get                  FR-019      red
test_pyproject_name_description_and_scripts                   FR-020/021  red
test_poetry_legacy_tables_are_the_fallback                    FR-020/021  red
test_package_json_still_wins_over_pyproject                   FR-020      guard
test_unparseable_pyproject_leaves_the_survey_running          FR-020      guard
test_httpexception_detail_and_positional_are_messages         FR-022/032  red
test_httpexception_dict_detail_contributes_no_message         FR-022      guard
test_tomllib_imported_at_module_level_with_the_floor_comment  NFR-002     red
===========================================================  ==========  =======

Every test drives the script through the conftest ``run_script`` helper. Nothing
here imports survey.py: it resolves ROOT from ``sys.argv`` at module import
(survey.py:13), so an import would freeze the wrong root before a test could set
one (GI-004).

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

    The decorator pass is gated on ``"python" in stack`` (survey.py:167), and the
    stack marker is a pyproject.toml on disk, so a tree holding only a .py file
    is surveyed as having no HTTP surface at all.
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
