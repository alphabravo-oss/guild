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


# --- Instruction PROSE names no model for a steerable agent (D-013) ---------
#
# The grep above sees only the dict-literal form (``"model": "opus"``), so it
# is blind to English sentences — and ``_ACTION_IMPERATIVES`` is nothing but
# English sentences. ``_format_imperative_header`` prepends the matching entry
# into the very ``instructions`` field whose response also carries the
# config-derived ``agent_config``, so a sentence asserting that
# ``foundry:teammate`` "carries model=opus" makes ONE response contradict
# itself the moment the option is set to something else — and the lead reads
# the imperative header first (GI-003, FR-009, AC-001, OT-002).
#
# Documenting a NON-steerable agent's pin stays legitimate: the assayer really
# does hold opus at every setting (AC-004), and the decompose/test spawns are
# ``general-purpose``. So each model literal binds to the NEAREST agent
# reference and only a steerable one fails — the same nearest-reference
# binding ``_documented_pins`` uses for start.md below.

_PROSE_MODEL_LITERAL_RE = re.compile(
    r"model\s*[=:]\s*['\"]?(?:%s)\b" % "|".join(fo.ACCEPTED_MODELS)
)
_QUALIFIED_AGENT_RE = re.compile(r"(?:foundry|forge):[a-z0-9-]+")

_STEERABLE_NAMES = frozenset(fo.STEERABLE_SUBAGENT_TYPES) | {
    name.split(":", 1)[1] for name in fo.STEERABLE_SUBAGENT_TYPES
}


def _agent_reference_re() -> re.Pattern[str]:
    """Match every way the imperatives name an agent.

    Derived from the imperatives themselves rather than hand-listed: an agent
    named in qualified form anywhere in the map also gets its bare form
    recognised ("the assayer's frontmatter ..."), so adding an agent to the map
    does not silently create a blind spot here.

    The steerable set is seeded unconditionally rather than derived, because
    ``foundry:flow-mapper`` has no spawn site in this server today (forge's
    plan.md spawns it) — deriving alone would leave the agent this guard exists
    to protect unrecognised until the day someone adds prose about it.
    """
    qualified = sorted(
        {
            name
            for text in fo._ACTION_IMPERATIVES.values()
            for name in _QUALIFIED_AGENT_RE.findall(text)
        }
        | set(fo.STEERABLE_SUBAGENT_TYPES)
    )
    bare = sorted({q.split(":", 1)[1] for q in qualified}, key=len, reverse=True)
    alternatives = (
        [re.escape(q) for q in qualified]
        + ["general-purpose"]
        + [rf"\b{re.escape(b)}\b" for b in bare]
    )
    return re.compile("|".join(alternatives))


_AGENT_REFERENCE_RE = _agent_reference_re()


def _steerable_model_claims(text: str) -> list[str]:
    """Model literals in ``text`` whose nearest agent reference is steerable."""
    refs = list(_AGENT_REFERENCE_RE.finditer(text))
    if not refs:
        return []
    claims = []
    for literal in _PROSE_MODEL_LITERAL_RE.finditer(text):
        nearest = min(refs, key=lambda r: abs(r.start() - literal.start()))
        if nearest.group(0) in _STEERABLE_NAMES:
            claims.append(f"{nearest.group(0)} ... {literal.group(0)}")
    return claims


def test_action_imperatives_name_no_model_for_a_steerable_agent() -> None:
    """No imperative may state a steerable agent's model as settled fact.

    Reproduced live with FOUNDRY_MODEL=fable: one Foundry-Next response carried
    ``agent_config {"model": "fable"}`` AND an instructions field asserting
    foundry:teammate carries model=opus. The server must not contradict itself
    about an agent the option can steer.
    """
    stray = {
        action: claims
        for action, text in sorted(fo._ACTION_IMPERATIVES.items())
        if (claims := _steerable_model_claims(text))
    }
    assert not stray, (
        "_ACTION_IMPERATIVES prose states a model for a steerable agent: "
        f"{stray} — a steerable agent's model comes from the returned "
        "agent_config / Foundry-Cast-Wave clause, never from frozen prose"
    )


def test_the_prose_guard_catches_the_claim_it_was_written_for() -> None:
    """Positive control: the scanner bites on D-013 and spares the legal cases.

    Without this, the guard above would still pass if the scanner silently
    stopped matching — which is exactly how the dict-literal grep let prose
    through in the first place.
    """
    assert _steerable_model_claims(
        "subagent_type='foundry:teammate', mode='bypassPermissions'. "
        "(foundry:teammate's frontmatter carries model=opus + effort=xhigh + all tools.)"
    ), "the exact D-013 sentence must fail this guard"
    assert _steerable_model_claims(
        "spawn Agent(subagent_type='foundry:flow-mapper') with model: fable"
    ), "the other steerable agent must be covered too"

    assert not _steerable_model_claims(
        "Spawn 4 parallel Agent(subagent_type='foundry:assayer', prompt='...'). "
        "(The assayer's frontmatter carries model=opus and effort=max.)"
    ), "documenting a FIXED-baseline agent's pin is accurate, not a violation"
    assert not _steerable_model_claims(
        "Per-Agent params: model='opus', subagent_type='general-purpose'"
    ), "the decompose/test spawns are outside the pilot and keep their baseline"
    assert not _steerable_model_claims(
        "spawn one Agent per casting: subagent_type='foundry:teammate' "
        "(frontmatter carries effort=xhigh + all tools)"
    ), "effort and tools are not model claims (NFR-002 keeps effort out of scope)"


@pytest.mark.parametrize("action", ["transition_to_cast", "transition_to_grind"])
def test_steerable_spawn_imperative_defers_to_the_returned_clause(action: str) -> None:
    """Dropping the literal is half the fix; the deferral must be stated.

    Silence is not an instruction (GI-002): an imperative that says nothing
    about model leaves the lead to improvise one, which is the same defect in
    a quieter form.
    """
    text = fo._ACTION_IMPERATIVES[action]
    assert "obey the model clause in the `instructions`" in text, (
        f"the {action} imperative must send the lead to the model clause the "
        f"MCP returned; got: {text!r}"
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


# --- Documented pins must match the agent files (D-011) ---------------------
#
# Outside the two MCP-driven spawn steps, start.md DOCUMENTS each agent's pin
# next to the agent file it names ("agents/tracer.md (sonnet)"). Documenting a
# pin is not deciding a model, so the guards above deliberately leave those
# lines alone — but a STALE pin is worse than no annotation at all: a lead
# following "model: sonnet" for an agent whose frontmatter now reads opus
# silently undoes the baseline raise. The agent file is the only source of
# truth (A-AUTO-011: a pin can never be made dynamic), so every alias this
# command prints beside an agent path is checked against it. A baseline change
# that forgets start.md is then a test failure, not a run-time surprise.

AGENTS_DIR = REPO_ROOT / "plugins" / "foundry" / "agents"

# Foundry's own agents only. ``plugins/forge/agents/spec-reviewer.md`` appears
# in the F0.5 roster as a bare identifier that is never resolved from here
# (Forge has no path reachable from ${CLAUDE_PLUGIN_ROOT}), and this casting
# does not read forge.
_AGENT_REF_RE = re.compile(r"(?<!forge/)agents/([a-z0-9-]+)\.md")


def _frontmatter_model(agent: str) -> str:
    """The ``model:`` an agent file declares, or ``""`` when it declares none."""
    path = AGENTS_DIR / f"{agent}.md"
    assert path.exists(), f"start.md names {path.name}, which does not exist"
    found = re.search(
        r"^model:\s*(\S+)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE
    )
    return found.group(1) if found else ""


def _documented_pins() -> list[tuple[int, str, str]]:
    """``(line_no, agent, alias)`` for every documented pin in start.md.

    Each alias binds to the NEAREST agent reference on its line, in either
    direction: the F2 roster writes the path first (``agents/tracer.md
    (sonnet)``) while F0 RESEARCH writes the alias first (``(model: sonnet,
    prompt: .../agents/researcher.md)``). Binding by proximity also keeps the
    TEST-01 row honest — it names ``spec-test-deriver.md`` beside its pin and
    ``test-observations-adjudicator.md`` at the far end of the same line, and
    that row is exactly where a stale pin last hid. An alias on a line with no
    agent reference is not a documented pin and is ignored.
    """
    pins: list[tuple[int, str, str]] = []
    lines = START_MD.read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        refs = list(_AGENT_REF_RE.finditer(line))
        if not refs:
            continue
        for alias in _MODEL_ALIAS_RE.finditer(line):
            nearest = min(refs, key=lambda r: abs(r.start() - alias.start()))
            pins.append((number, nearest.group(1), alias.group(0)))
    return pins


def test_start_md_documented_pins_match_agent_frontmatter() -> None:
    """Every alias documented beside an agent path is that agent's real pin.

    The F0.6 PATTERN MAPPING prose kept saying "model: sonnet" for
    ``pattern-mapper`` after its frontmatter moved to opus — and
    ``pattern-mapper`` is the agent whose wrong analog propagates into every
    casting prompt. Staleness is the defect, not the annotation, so this
    checks the annotations rather than banning them.
    """
    pins = _documented_pins()
    assert len(pins) >= 10, (
        f"only {len(pins)} documented pin(s) found — the F2 INSPECT and AGENT "
        "PROMPTS rosters carry more than that, so the scan is matching too "
        "little to prove anything"
    )
    drift = [
        f"start.md:{number} documents {agent} as '{alias}', but "
        f"agents/{agent}.md declares '{_frontmatter_model(agent) or '<none>'}'"
        for number, agent, alias in pins
        if alias != _frontmatter_model(agent)
    ]
    assert not drift, "stale model literal(s) in start.md prose:\n  " + "\n  ".join(
        drift
    )


def test_f06_pattern_map_spawn_defers_to_the_agent_file() -> None:
    """F0.6 points the lead at the agent file instead of restating a model.

    F0.6 is the one spawn step with no MCP-returned ``agent_config`` to obey —
    the server knows nothing about ``pattern-mapper`` — so deference here means
    the agent file's own pin. Naming a model in this prose instead would be a
    second source of truth for a value that already lives one file away, which
    is precisely how the sonnet/opus drift got in.
    """
    step = _spawn_step("### F0.6: PATTERN MAPPING", "pattern-mapper")
    assert "take the pin from the `model:` line of that same agent file" in step, (
        "the F0.6 spawn step must send the lead to pattern-mapper.md's own "
        f"frontmatter for the model; got: {step!r}"
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
