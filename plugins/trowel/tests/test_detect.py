import json

import detect


def test_go_module(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    d = detect.detect(tmp_path)
    assert [c["cmd"] for c in d["build"]] == ["go build ./..."]
    assert [c["cmd"] for c in d["lint"]] == ["go vet ./..."]
    assert [c["cmd"] for c in d["test"]] == ["go test ./..."]


def test_node_scripts_and_package_manager(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"build": "tsc", "lint": "eslint .", "test": "vitest run"}}))
    (tmp_path / "pnpm-lock.yaml").write_text("")
    d = detect.detect(tmp_path)
    assert [c["cmd"] for c in d["build"]] == ["pnpm run build"]
    assert [c["cmd"] for c in d["lint"]] == ["pnpm run lint"]
    assert [c["cmd"] for c in d["test"]] == ["pnpm test"]


def test_npm_default_test_script_is_not_a_test(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test": "echo \"Error: no test specified\" && exit 1"}}))
    assert detect.detect(tmp_path)["test"] == []


def test_python_pytest_and_ruff(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n[tool.ruff]\n")
    d = detect.detect(tmp_path)
    assert [c["cmd"] for c in d["test"]] == ["python3 -m pytest -q"]
    assert [c["cmd"] for c in d["lint"]] == ["ruff check ."]


def test_makefile_only_without_language_markers(tmp_path):
    (tmp_path / "Makefile").write_text("build:\n\techo\ntest: build\n\techo\nvet:\n\techo\n")
    d = detect.detect(tmp_path)
    assert [c["cmd"] for c in d["build"]] == ["make build"]
    assert [c["cmd"] for c in d["lint"]] == ["make vet"]
    (tmp_path / "go.mod").write_text("module x\n")
    assert [c["cmd"] for c in detect.detect(tmp_path)["build"]] == ["go build ./..."]


def test_subdirectory_projects_carry_their_cwd(tmp_path):
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "package.json").write_text(json.dumps({"scripts": {"build": "vite build"}}))
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "package.json").write_text("{}")
    d = detect.detect(tmp_path)
    assert d["build"] == [{"cmd": "npm run build", "cwd": "web", "why": "web/package.json scripts"}]


# --- the integration check ---------------------------------------------------
def test_spec_without_an_integration_check_asks_for_none(tmp_path):
    assert detect.integration(tmp_path, "- FR-1: add returns the sum\n") == {"found": None, "asked": False}


def test_spec_command_that_matches_a_makefile_target(tmp_path):
    (tmp_path / "Makefile").write_text("e2e:\n\tdocker compose up --abort-on-container-exit\nbuild:\n\techo\n")
    r = detect.integration(tmp_path, "- AC-1: the end-to-end suite passes: run `make e2e`.\n")["found"]
    assert r["command"] == "make e2e" and "the spec names `make e2e`" in r["source"]
    assert {"what": "make on PATH", "command": "command -v make"} in r["needs"]
    assert any(n["command"] == "docker info" for n in r["needs"])
    assert r["dry_run"] == "make -n e2e" and r["exists"]


def test_mage_target_the_build_will_write_inherits_docker_from_its_siblings(tmp_path):
    (tmp_path / "magefile.go").write_text("//go:build mage\npackage main\n\nfunc Smoke() error {\n\treturn sh.Run(\"docker\", \"run\")\n}\n")
    r = detect.integration(tmp_path, "- AC-7: an integration suite, run with `mage metrics`, boots a cluster.\n")["found"]
    assert r["command"] == "mage metrics" and not r["exists"] and "the build writes it" in r["source"]
    assert r["dry_run"] == "mage -l" and any(n["command"] == "docker info" for n in r["needs"])


def test_go_test_command_on_an_integration_path(tmp_path):
    (tmp_path / "tests" / "e2e").mkdir(parents=True)
    (tmp_path / "tests" / "e2e" / "x_test.go").write_text("package e2e\n")
    r = detect.integration(tmp_path, "Integration: `go test -tags e2e ./tests/e2e/...` must pass.\n")["found"]
    assert r["command"] == "go test -tags e2e ./tests/e2e/..." and r["dry_run"] == "go vet -tags e2e ./tests/e2e/..."
    assert not any(n["command"] == "docker info" for n in r["needs"])


def test_one_integration_target_and_no_command_in_the_spec(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"test:e2e": "playwright test", "build": "vite build"}}))
    r = detect.integration(tmp_path, "The acceptance tests must cover login.\n")["found"]
    assert r["command"] == "npm run test:e2e" and "names no command" in r["source"]


def test_asked_but_nothing_to_match_needs_the_finder(tmp_path):
    (tmp_path / "Makefile").write_text("e2e:\n\techo\nintegration:\n\techo\n")
    assert detect.integration(tmp_path, "An integration test proves it.\n") == {"found": None, "asked": True}


def test_unit_test_command_named_for_contrast_is_not_the_integration_check(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    (tmp_path / "magefile.go").write_text("//go:build mage\npackage main\n\nfunc Test() error {\n\treturn sh.RunV(\"go\", \"test\", \"./...\")\n}\n")
    spec = ("- **Verification:** mage metrics (needs Docker)\n"
            "Integration suites live in `tests/<tag>/`. Unit tests run with `mage test` (`go test ./...`).\n")
    r = detect.integration(tmp_path, spec)["found"]
    assert r["command"] == "mage metrics" and not r["exists"]


def test_make_target_named_integration_with_no_makefile_is_one_the_build_writes(tmp_path):
    r = detect.integration(tmp_path, "- **Verification:** `make test-integration` and `make e2e`\n")["found"]
    assert r["command"] == "make test-integration" and not r["exists"]
    assert r["dry_run"] is None and "the build writes it" in r["source"]


def test_a_target_named_for_integration_beats_a_dev_loop_named_first(tmp_path):
    spec = ("- **Verification:** `make check` and `make dev-up` then curl\n"
            "- **Verification:** `make test-integration` and `make e2e`\n")
    r = detect.integration(tmp_path, spec)["found"]
    assert r["command"] == "make test-integration" and not r["exists"]


def test_bare_run_command_on_a_docker_line(tmp_path):
    (tmp_path / "Makefile").write_text("e2e:\n\tdocker compose up\n")
    r = detect.integration(tmp_path, "- Run make e2e: it boots the stack in Docker and must pass.\n")["found"]
    assert r["command"] == "make e2e" and r["exists"]


def test_only_the_unit_test_named_falls_through_to_nothing(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    assert detect.integration(tmp_path, "Integration is out of scope; run `go test ./...`.\n") == {"found": None, "asked": True}
