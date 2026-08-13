"""Model-selection config tests — the foundry MCP server's model policy.

The `model` userConfig option reaches this server as the FOUNDRY_MODEL
environment variable (``${user_config.model}`` substituted into the MCP server
declaration's ``env``). This module pins the behavior that policy must have:

  - FOUNDRY_MODEL unset          -> no ``model`` key is contributed anywhere
  - FOUNDRY_MODEL == ""          -> identical to unset (the harness substitutes
                                    an unset option as the EMPTY STRING, not as
                                    an absent variable, so this is the load-
                                    bearing case rather than a defensive one)
  - FOUNDRY_MODEL set + accepted -> ONLY the steerable agents carry it
  - fixed-baseline agents        -> same config at every setting
  - value outside the allowlist  -> refused, with the accepted set named
  - relaxed frontmatter regexes  -> still fail on a missing/malformed line

The last group guards the two sibling assertions in ``test_intent_coverage.py``
and ``test_spec_test_deriver.py``: those were relaxed from a single literal to
an allowlist, and relaxing an assertion is only safe if something proves it can
still fail.

Run with the rest of the suite: ``uv run --with pytest pytest`` from
``plugins/foundry/mcp-server``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from foundry_mcp.tools import foundry_orchestrator as fo
from foundry_mcp.tools import foundry_spawn as fs
from foundry_mcp.tools import foundry_state


REPO_ROOT = Path(__file__).resolve().parents[4]
TESTS_DIR = Path(__file__).resolve().parent

ORCHESTRATOR_SRC = (
    REPO_ROOT
    / "plugins" / "foundry" / "mcp-server" / "src" / "foundry_mcp" / "tools"
    / "foundry_orchestrator.py"
)
START_MD = REPO_ROOT / "plugins" / "foundry" / "commands" / "start.md"

# The exact pattern both sibling test modules must use for their frontmatter
# ``model:`` assertion. Declared once here so a drift guard can compare.
FRONTMATTER_MODEL_PATTERN = r"^model:\s*(opus|sonnet|haiku|fable|inherit)\s*$"

# Agents that hold a fixed baseline: the option must never move them.
FIXED_BASELINE_AGENTS = (
    "foundry:assayer",
    "foundry:intent-carrier",
    "foundry:test-observations-adjudicator",
    "foundry:pattern-mapper",
    "foundry:spec-test-deriver",
)


# --------------------------------------------------------------------------- #
# Fixtures & helpers
# --------------------------------------------------------------------------- #


@pytest.fixture
def model_env(monkeypatch):
    """Return a setter for FOUNDRY_MODEL; ``None`` removes it entirely.

    The ambient environment may already carry FOUNDRY_MODEL (this server runs
    inside a real Claude Code session), so every test states its own value
    rather than inheriting one.
    """

    def _set(value: str | None) -> None:
        if value is None:
            monkeypatch.delenv(fo.MODEL_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(fo.MODEL_ENV_VAR, value)

    _set(None)
    return _set


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    """Activate a foundry run under tmp_path; yield (project_root, fdir).

    Mirrors ``test_orchestrator_gates.run_env`` — ``_check_active_teams`` is
    patched inactive so routing never depends on the ambient tmux session.
    """
    project_root = tmp_path
    run_name = "c2-model-config-run"
    fdir = project_root / "foundry-archive" / run_name
    (fdir / "castings").mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        fo,
        "_check_active_teams",
        lambda _pr: {"active": False, "teams": [], "live_panes": []},
    )

    foundry_state.set_active_run(run_name)
    try:
        yield str(project_root), fdir
    finally:
        foundry_state.clear_active_run()


def _write_state(fdir: Path, phase: str, **extra) -> None:
    state = {"phase": phase}
    state.update(extra)
    (fdir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _write_wave(fdir: Path, casting_id: int = 1) -> None:
    """Minimal manifest + prompt file so Foundry-Cast-Wave can resolve wave 1."""
    (fdir / "castings").mkdir(parents=True, exist_ok=True)
    (fdir / "castings" / "manifest.json").write_text(
        json.dumps(
            {
                "castings": [{"id": casting_id, "key_files": []}],
                "waves": [{"wave": 1, "casting_ids": [casting_id]}],
            }
        ),
        encoding="utf-8",
    )
    (fdir / "castings" / f"casting-{casting_id}-prompt.md").write_text(
        "casting prompt body\n", encoding="utf-8"
    )


def _spawn_log_entries(fdir: Path) -> list[dict]:
    log = fdir / "spawns.log"
    if not log.exists():
        return []
    return [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# --------------------------------------------------------------------------- #
# The accepted set and the steerable set
# --------------------------------------------------------------------------- #


def test_accepted_models_are_exactly_the_five_aliases() -> None:
    """Aliases + inherit — no more, no fewer."""
    assert set(fo.ACCEPTED_MODELS) == {
        "opus", "sonnet", "haiku", "fable", "inherit"
    }, fo.ACCEPTED_MODELS


def test_steerable_set_is_exactly_teammate_and_flow_mapper() -> None:
    """Only the good fits follow the option — foundry's share is these two."""
    assert set(fo.STEERABLE_SUBAGENT_TYPES) == {
        "foundry:teammate", "foundry:flow-mapper"
    }, fo.STEERABLE_SUBAGENT_TYPES


def test_env_var_name_matches_the_manifest_contract() -> None:
    """The manifest substitutes ``${user_config.model}`` into this exact name."""
    assert fo.MODEL_ENV_VAR == "FOUNDRY_MODEL"


# --------------------------------------------------------------------------- #
# configured_model(): unset / empty / accepted / rejected
# --------------------------------------------------------------------------- #


def test_absent_variable_resolves_empty(model_env) -> None:
    model_env(None)
    assert fo.configured_model() == ""


def test_empty_string_resolves_empty(model_env) -> None:
    """An unset option substitutes as "" — it must not become a spawn param."""
    model_env("")
    assert fo.configured_model() == ""


def test_whitespace_only_resolves_empty(model_env) -> None:
    model_env("   ")
    assert fo.configured_model() == ""


@pytest.mark.parametrize("value", ["opus", "sonnet", "haiku", "fable", "inherit"])
def test_every_accepted_value_is_forwarded(model_env, value: str) -> None:
    """``inherit`` included: it is a real value, not a synonym for unset."""
    model_env(value)
    assert fo.configured_model() == value


def test_surrounding_whitespace_is_tolerated(model_env) -> None:
    model_env("  fable  ")
    assert fo.configured_model() == "fable"


@pytest.mark.parametrize("bad", ["gpt-4", "claude-opus-5", "OPUS", "fabel", "none"])
def test_value_outside_the_set_is_refused_naming_the_set(model_env, bad: str) -> None:
    """Refusal is loud and self-documenting: the message names every alias."""
    model_env(bad)
    with pytest.raises(ValueError) as exc:
        fo.configured_model()
    message = str(exc.value)
    for accepted in fo.ACCEPTED_MODELS:
        assert accepted in message, f"error message omits {accepted!r}: {message}"
    assert fo.MODEL_ENV_VAR in message, message


# --------------------------------------------------------------------------- #
# agent_model(): who is steerable, and the never-emit-an-empty-model rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("steerable", ["foundry:teammate", "foundry:flow-mapper"])
def test_steerable_agent_carries_the_configured_model(model_env, steerable: str) -> None:
    model_env("fable")
    assert fo.agent_model(steerable) == {"model": "fable"}


@pytest.mark.parametrize("steerable", ["foundry:teammate", "foundry:flow-mapper"])
@pytest.mark.parametrize("unconfigured", [None, "", "  "])
def test_steerable_agent_emits_no_key_when_unconfigured(
    model_env, steerable: str, unconfigured: str | None
) -> None:
    """No key at all — never ``{"model": ""}``, which is a malformed spawn."""
    model_env(unconfigured)
    assert fo.agent_model(steerable) == {}


@pytest.mark.parametrize("agent", FIXED_BASELINE_AGENTS)
@pytest.mark.parametrize("setting", [None, "", "opus", "sonnet", "haiku", "fable", "inherit"])
def test_fixed_baseline_agents_are_identical_at_every_setting(
    model_env, agent: str, setting: str | None
) -> None:
    """These five hold opus at every setting; the option cannot reach them."""
    model_env(setting)
    assert fo.agent_model(agent) == {}
    assert fo.agent_model(agent, baseline="opus") == {"model": "opus"}


@pytest.mark.parametrize("setting", [None, "", "opus", "sonnet", "haiku", "fable", "inherit"])
def test_general_purpose_keeps_its_opus_baseline_at_every_setting(
    model_env, setting: str | None
) -> None:
    """The decompose / test / temper spawns are outside the pilot entirely."""
    model_env(setting)
    assert fo.agent_model("general-purpose", baseline="opus") == {"model": "opus"}


def test_baseline_is_not_steerable_for_an_unknown_agent(model_env) -> None:
    model_env("fable")
    assert fo.agent_model("foundry:tracer", baseline="sonnet") == {"model": "sonnet"}


# --------------------------------------------------------------------------- #
# Foundry-Next agent_config — the emitted dicts
# --------------------------------------------------------------------------- #


def test_cast_config_unset_is_byte_identical_to_the_pre_option_shape(
    run_env, model_env
) -> None:
    project_root, fdir = run_env
    model_env(None)
    _write_state(fdir, "F1")

    result = fo._compute_next_action(project_root)

    assert result["details"]["agent_config"] == {
        "subagent_type": "foundry:teammate",
        "mode": "bypassPermissions",
    }


@pytest.mark.parametrize("value", ["opus", "sonnet", "haiku", "fable", "inherit"])
def test_cast_config_carries_the_configured_model(run_env, model_env, value: str) -> None:
    project_root, fdir = run_env
    model_env(value)
    _write_state(fdir, "F1")

    config = fo._compute_next_action(project_root)["details"]["agent_config"]

    assert config["model"] == value
    assert config["subagent_type"] == "foundry:teammate"


def test_grind_config_carries_the_configured_model(run_env, model_env) -> None:
    project_root, fdir = run_env
    model_env("fable")
    _write_state(fdir, "F3")
    (fdir / "defects.json").write_text(
        json.dumps({"defects": [{"id": "D-1", "status": "open"}]}), encoding="utf-8"
    )

    config = fo._compute_next_action(project_root)["details"]["agent_config"]

    assert config == {
        "subagent_type": "foundry:teammate",
        "model": "fable",
        "mode": "bypassPermissions",
    }


@pytest.mark.parametrize("setting", [None, "fable"])
def test_decompose_config_holds_opus_at_every_setting(
    run_env, model_env, setting: str | None
) -> None:
    """The F0.5 decompose agent is general-purpose — outside the pilot."""
    project_root, fdir = run_env
    model_env(setting)
    _write_state(fdir, "F0")

    config = fo._compute_next_action(project_root)["details"]["agent_config"]

    assert config == {
        "model": "opus",
        "subagent_type": "general-purpose",
        "mode": "bypassPermissions",
        "run_in_background": True,
    }


@pytest.mark.parametrize("setting", [None, "fable"])
def test_inspect_stream_configs_hold_their_baselines(
    run_env, model_env, setting: str | None
) -> None:
    """TRACE/PROVE carry no model key; TEST holds opus. None of them move."""
    project_root, fdir = run_env
    model_env(setting)
    _write_state(fdir, "F1")
    (fdir / ".cast-complete").write_text("done\n", encoding="utf-8")

    configs = fo._compute_next_action(project_root)["details"]["agent_configs"]

    assert "model" not in configs["trace"]
    assert "model" not in configs["prove"]
    assert configs["test"] == {
        "model": "opus",
        "subagent_type": "general-purpose",
    }


def test_next_action_refuses_a_bad_value_naming_the_set(run_env, model_env) -> None:
    """A misconfiguration surfaces at the first Foundry-Next, not mid-run."""
    project_root, fdir = run_env
    model_env("gpt-4")
    _write_state(fdir, "F1")

    with pytest.raises(ValueError) as exc:
        fo._compute_next_action(project_root)
    for accepted in fo.ACCEPTED_MODELS:
        assert accepted in str(exc.value)


# --------------------------------------------------------------------------- #
# Foundry-Cast-Wave / Foundry-Spawn-Teammate — instructions + spawn record
# --------------------------------------------------------------------------- #


def test_cast_wave_tells_the_lead_to_pass_nothing_when_unset(run_env, model_env) -> None:
    project_root, fdir = run_env
    model_env(None)
    _write_wave(fdir)

    result = fs.foundry_cast_wave(1, "cast", project_root)

    assert result["ok"] is True
    assert "model" not in result
    assert "Pass NO model parameter" in result["instructions"]
    # The old claim asserted the frontmatter pin as if it were the decision.
    assert "frontmatter sets model=opus" not in result["instructions"]


def test_cast_wave_names_the_configured_model(run_env, model_env) -> None:
    project_root, fdir = run_env
    model_env("fable")
    _write_wave(fdir)

    result = fs.foundry_cast_wave(1, "cast", project_root)

    assert result["model"] == "fable"
    assert "model='fable'" in result["instructions"]
    assert "Pass NO model parameter" not in result["instructions"]


def test_cast_wave_spawn_record_names_the_model(run_env, model_env) -> None:
    """The run's spawn log shows which model the steerable agent was asked for."""
    project_root, fdir = run_env
    model_env("fable")
    _write_wave(fdir)

    fs.foundry_cast_wave(1, "cast", project_root)

    entries = _spawn_log_entries(fdir)
    assert entries and all(e["model"] == "fable" for e in entries)


def test_cast_wave_spawn_record_omits_the_model_when_unset(run_env, model_env) -> None:
    project_root, fdir = run_env
    model_env(None)
    _write_wave(fdir)

    fs.foundry_cast_wave(1, "cast", project_root)

    entries = _spawn_log_entries(fdir)
    assert entries and all("model" not in e for e in entries)


def test_spawn_teammate_carries_the_model_on_the_single_dispatch_path(
    run_env, model_env
) -> None:
    """GRIND / single re-dispatch must not silently drop the configured model."""
    project_root, fdir = run_env
    model_env("fable")
    _write_wave(fdir)

    result = fs.foundry_spawn_teammate(1, "cast", project_root)

    assert result["ok"] is True
    assert result["model"] == "fable"
    assert "model='fable'" in result["instructions"]
    assert _spawn_log_entries(fdir)[0]["model"] == "fable"


def test_spawn_teammate_is_unchanged_when_unset(run_env, model_env) -> None:
    project_root, fdir = run_env
    model_env(None)
    _write_wave(fdir)

    result = fs.foundry_spawn_teammate(1, "cast", project_root)

    assert "model" not in result
    assert "model=" not in result["instructions"]
    assert "model" not in _spawn_log_entries(fdir)[0]


# --------------------------------------------------------------------------- #
# Source-level guards
# --------------------------------------------------------------------------- #


def test_orchestrator_has_no_hardcoded_model_literal() -> None:
    """Every model decision routes through ``agent_model`` — no second source."""
    source = ORCHESTRATOR_SRC.read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    stray = re.findall(r'"model":\s*"[a-z]', code)
    assert not stray, (
        f"hardcoded model literal(s) reintroduced in {ORCHESTRATOR_SRC.name}: {stray}"
    )


def test_start_md_defers_to_the_mcp_returned_model() -> None:
    """The lead is told to obey what the server returns, not to never override."""
    text = START_MD.read_text(encoding="utf-8")
    assert "don't override" not in text, (
        "start.md still instructs the lead not to override; the MCP server owns "
        "the model decision and the prose must defer to what it returns"
    )
    assert "disable-model-invocation:" in text
    assert "hide-from-slash-command-tool" not in text


# --- The two steerable spawn steps carry no model of their own (GI-003) ------
#
# ``foundry:teammate`` is steerable, and it is spawned from exactly two places
# in this command: the F1 CAST step and the F3 GRIND step. Both must take the
# model from what the MCP returned. The guard below is scoped to those two
# steps rather than the whole file because the F2/AGENT-PROMPTS sections
# legitimately DOCUMENT other agents' frontmatter pins ("agents/tracer.md
# (sonnet)"), and documenting a pin is not deciding a model.

_SPAWN_STEP_SECTIONS = {
    "F1 CAST": ("### F1: CAST", "Foundry-Cast-Wave"),
    "F3 GRIND": ("### F3: GRIND", "Foundry-Spawn-Teammate"),
}

# A bare parenthesised alias — the "spawn Agent (opus)" directive form.
_BARE_PAREN_MODEL_RE = re.compile(
    r"\((?:%s)\)" % "|".join(fo.ACCEPTED_MODELS)
)
_MODEL_ALIAS_RE = re.compile(r"\b(?:%s)\b" % "|".join(fo.ACCEPTED_MODELS))
_BACKTICKED_RE = re.compile(r"`[^`]*`")


def _spawn_step(section_heading: str, step_marker: str) -> str:
    """Return the one line of ``section_heading`` that spawns the teammate."""
    text = START_MD.read_text(encoding="utf-8")
    start = text.index(section_heading)
    nxt = text.find("\n### ", start + 1)
    section = text[start : nxt if nxt != -1 else len(text)]
    steps = [ln for ln in section.splitlines() if step_marker in ln]
    assert steps, f"no {step_marker} spawn step found under {section_heading}"
    return "\n".join(steps)


@pytest.mark.parametrize("label", sorted(_SPAWN_STEP_SECTIONS))
def test_spawn_step_has_no_bare_parenthesised_model(label: str) -> None:
    """`spawn Agent (opus)` is the lead deciding a model. The MCP decides.

    This is the exact regression that made the F3 GRIND step contradict the
    F1 CAST step: a parenthesised literal reads as a spawn parameter, so the
    lead passes it instead of the model the server returned (GI-003, FR-009,
    AC-001, OT-002).
    """
    step = _spawn_step(*_SPAWN_STEP_SECTIONS[label])
    stray = _BARE_PAREN_MODEL_RE.findall(step)
    assert not stray, (
        f"{label} spawn step hardcodes {stray} — the model must come from what "
        "Foundry-Cast-Wave / Foundry-Spawn-Teammate returned, never from this prose"
    )


@pytest.mark.parametrize("label", sorted(_SPAWN_STEP_SECTIONS))
def test_spawn_step_names_a_model_only_inside_a_code_span(label: str) -> None:
    """Any alias in these steps must be a quoted frontmatter-pin reference.

    A pin reference reads as ``(`model=opus + effort=xhigh`)`` — inside a code
    span, describing the floor that applies when nothing is configured. Bare
    prose naming a model is an instruction, and instructions about model belong
    to the MCP server. Catches `spawn Agent with model=fable`, which the
    parenthesis guard above would miss.
    """
    step = _spawn_step(*_SPAWN_STEP_SECTIONS[label])
    uncoded = _BACKTICKED_RE.sub(" ", step)
    stray = _MODEL_ALIAS_RE.findall(uncoded)
    assert not stray, (
        f"{label} spawn step names {stray} outside a code span; a model literal "
        "in lead prose is a second source of truth for a policy the MCP owns"
    )


@pytest.mark.parametrize("label", sorted(_SPAWN_STEP_SECTIONS))
def test_spawn_step_defers_to_the_returned_instructions(label: str) -> None:
    """Removing the literal is only half the fix — the deferral must be stated.

    Without this the guards above pass for a step that says nothing about model
    at all, and the lead falls back to guessing (GI-002: absence of guidance is
    not the same as an instruction to pass nothing).
    """
    step = _spawn_step(*_SPAWN_STEP_SECTIONS[label])
    assert "obey the returned `instructions` clause" in step, (
        f"{label} spawn step must tell the lead to obey the model clause the "
        f"MCP returned; got: {step!r}"
    )


# --------------------------------------------------------------------------- #
# The relaxed frontmatter assertions still bite
# --------------------------------------------------------------------------- #


def _front(*lines: str) -> str:
    """Build a frontmatter body from the given lines, id/effort always present."""
    return "\n".join(("id: X-01", *lines, "effort: high")) + "\n"


@pytest.mark.parametrize("value", ["opus", "sonnet", "haiku", "fable", "inherit"])
def test_relaxed_pattern_accepts_every_alias(value: str) -> None:
    assert re.search(
        FRONTMATTER_MODEL_PATTERN, _front(f"model: {value}"), re.MULTILINE
    )


@pytest.mark.parametrize(
    "front",
    [
        pytest.param(_front(), id="line-absent"),
        pytest.param(_front("model:"), id="key-with-no-value"),
        pytest.param(_front("model:    "), id="whitespace-only-value"),
        pytest.param(_front("model: gpt-4"), id="unrecognised-value"),
        pytest.param(_front("model: opus sonnet"), id="two-values-one-line"),
        pytest.param(_front("  model: opus"), id="indented-key"),
        pytest.param(_front("models: opus"), id="misspelled-key"),
    ],
)
def test_relaxed_pattern_still_fails_on_missing_or_malformed(front: str) -> None:
    """Relaxing to an allowlist must not weaken it into 'anything goes'."""
    assert not re.search(FRONTMATTER_MODEL_PATTERN, front, re.MULTILINE), front


@pytest.mark.parametrize(
    "module", ["test_intent_coverage.py", "test_spec_test_deriver.py"]
)
def test_sibling_modules_use_the_guarded_pattern(module: str) -> None:
    """Drift guard: the pattern proven above is the one actually asserted."""
    source = (TESTS_DIR / module).read_text(encoding="utf-8")
    assert FRONTMATTER_MODEL_PATTERN in source, (
        f"{module} does not use the allowlist pattern this module guards; "
        "the two must not drift apart"
    )
