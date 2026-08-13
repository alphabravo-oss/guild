"""Repo-wide YAML parse sweep over every plugins/*/agents/*.md frontmatter.

Why this module exists (D-014 -> D-018)
---------------------------------------
The sonnet->opus repin of `spec-test-deriver` shipped INERT. Its `description`
scalar was unquoted and contained a ": " sequence ("Code-blind: reads spec
only"), which YAML rejects with "mapping values are not allowed here" -- so the
harness dropped the ENTIRE frontmatter mapping and the agent ran on the
inherited session model rather than opus. `intent-carrier` carried the
identical bug ("<Appendix: Interview Transcript>").

Nothing caught it. Every frontmatter assertion in this suite is a line-anchored
regex over raw text, and a regex matches `model: opus` happily on a file YAML
refuses to load. `claude plugin validate` checks plugin.json only. This module
closes that gap: it parses the frontmatter as a mapping instead of grepping it,
so a file the harness would reject fails here first.

Why a hand-rolled parser
------------------------
pyyaml is not available to this suite's runner -- `uv run --with pytest pytest`
provides pytest and the project's own dependencies (mcp, jsonschema) and
nothing else, and the documented invocation is not changed by this module. A
`pytest.importorskip("yaml")` sweep would skip by default, which is no guard at
all -- exactly the hole D-018 names.

So `_parse_frontmatter` below is a deliberately strict parser for the flat
`key: value` subset that agent frontmatter actually uses (verified: all agent
files are un-indented, tab-free, and use only scalars and flow sequences). It
REJECTS anything it cannot confidently accept, including the unquoted-": "
class that broke D-014. Being stricter than YAML is the correct failure
direction here: the remedy is always to quote the scalar.

The hand-rolled parser is kept honest by
`test_strict_parser_agrees_with_pyyaml_across_the_corpus`, which runs the real
thing when pyyaml happens to be importable (`uv run --with pytest --with pyyaml
pytest`) and asserts the two agree. Without that cross-check this module would
be repeating D-018's own mistake -- a hand-rolled text guard standing in for a
real parse.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

# tests -> mcp-server -> foundry -> plugins -> repo root
REPO_ROOT = Path(__file__).resolve().parents[4]
AGENT_GLOB = "plugins/*/agents/*.md"

# A-014 "Aliases + inherit". The sub-agents docs also permit a full model ID
# (e.g. `claude-opus-5`), but this repo pins floating aliases only -- a dated
# snapshot would silently freeze an agent at a retired model. There is no
# allowlisted exception today; add one here with a comment if a full ID ever
# becomes deliberate.
ACCEPTED_MODELS = frozenset({"opus", "sonnet", "haiku", "fable", "inherit"})

# A-AUTO-004 counted 38 agent definitions. The floor guards against a glob that
# silently matches nothing -- a sweep over an empty list passes vacuously.
MIN_EXPECTED_AGENTS = 30

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.-]*):(?:[ ]+(.*))?$")
DOUBLE_QUOTED_RE = re.compile(r'^"(?:[^"\\]|\\.)*"$')
SINGLE_QUOTED_RE = re.compile(r"^'(?:[^']|'')*'$")
FLOW_ITEM_RE = re.compile(
    r'"(?:[^"\\]|\\.)*"'  # double-quoted item
    r"|'(?:[^']|'')*'"  # single-quoted item
    r"|[^,\[\]{}]+"  # plain item
)
# Characters that cannot open a plain (unquoted) YAML scalar. Quoted scalars
# and flow sequences are dispatched before this check, so their openers are
# deliberately absent.
PLAIN_SCALAR_FORBIDDEN_FIRST = frozenset("]{},&*!|>%@`#")


class FrontmatterError(Exception):
    """The frontmatter block is not something YAML would load as a mapping."""


def _extract_frontmatter(text: str) -> str:
    match = FRONTMATTER_RE.match(text)
    if not match:
        raise FrontmatterError(
            "no leading '---' frontmatter block; an agent definition must open "
            "with one or the harness has no model/tools/description to read"
        )
    return match.group(1)


def _parse_scalar(raw: str, lineno: int):
    if raw.startswith("["):
        return _parse_flow_sequence(raw, lineno)
    if raw.startswith('"'):
        if not DOUBLE_QUOTED_RE.match(raw):
            raise FrontmatterError(
                f"line {lineno}: unterminated or invalid double-quoted scalar: {raw!r}"
            )
        return raw[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if raw.startswith("'"):
        if not SINGLE_QUOTED_RE.match(raw):
            raise FrontmatterError(
                f"line {lineno}: unterminated or invalid single-quoted scalar: {raw!r}"
            )
        return raw[1:-1].replace("''", "'")
    if raw[0] in PLAIN_SCALAR_FORBIDDEN_FIRST:
        raise FrontmatterError(
            f"line {lineno}: plain scalar opens with YAML indicator {raw[0]!r}; "
            f"quote the value: {raw!r}"
        )
    if raw[:2] in ("- ", "? ", ": ") or raw in ("-", "?", ":"):
        raise FrontmatterError(
            f"line {lineno}: plain scalar opens with a YAML indicator: {raw!r}"
        )
    # THE D-014 CLASS. In block context a plain scalar cannot contain ": " --
    # YAML reads it as the start of a nested mapping and raises "mapping values
    # are not allowed here", discarding the whole frontmatter.
    if ": " in raw:
        raise FrontmatterError(
            f"line {lineno}: unquoted plain scalar contains ': ', which YAML "
            f"rejects with \"mapping values are not allowed here\" -- the whole "
            f"frontmatter mapping is then dropped and any model: pin becomes "
            f"inert. Wrap the value in double quotes. Value: {raw!r}"
        )
    if raw.endswith(":"):
        raise FrontmatterError(
            f"line {lineno}: unquoted plain scalar ends with ':': {raw!r}"
        )
    if " #" in raw:
        raise FrontmatterError(
            f"line {lineno}: unquoted plain scalar contains ' #', which starts a "
            f"YAML comment and truncates the value: {raw!r}"
        )
    if "\t" in raw:
        raise FrontmatterError(f"line {lineno}: tab inside plain scalar: {raw!r}")
    return raw.rstrip()


def _parse_flow_sequence(raw: str, lineno: int) -> list:
    if not raw.endswith("]"):
        raise FrontmatterError(f"line {lineno}: unterminated flow sequence: {raw!r}")
    inner = raw[1:-1].strip()
    if not inner:
        return []
    items, pos = [], 0
    while True:
        match = FLOW_ITEM_RE.match(inner, pos)
        if not match:
            raise FrontmatterError(
                f"line {lineno}: malformed flow-sequence item at offset {pos}: {raw!r}"
            )
        items.append(_parse_scalar(match.group(0).strip(), lineno))
        pos = match.end()
        if pos == len(inner):
            return items
        if inner[pos] != ",":
            raise FrontmatterError(
                f"line {lineno}: expected ',' at offset {pos} in flow sequence: {raw!r}"
            )
        pos += 1


def _parse_frontmatter(block: str) -> dict:
    """Parse the flat `key: value` subset agent frontmatter uses.

    Raises FrontmatterError on anything YAML would reject -- and on a few
    constructs YAML allows but this corpus does not use (nesting, block
    sequences), because accepting them silently would mean not really checking.
    """
    parsed: dict = {}
    for lineno, line in enumerate(block.split("\n"), start=1):
        if not line.strip():
            continue
        if line[0] in " \t":
            raise FrontmatterError(
                f"line {lineno}: indented/nested frontmatter is not used by agent "
                f"definitions and is not parsed here: {line!r}"
            )
        match = KEY_RE.match(line)
        if not match:
            raise FrontmatterError(
                f"line {lineno}: not a top-level 'key: value' mapping entry: {line!r}"
            )
        key, raw = match.group(1), match.group(2)
        parsed[key] = None if not raw else _parse_scalar(raw, lineno)
    return parsed


def _agent_files() -> list[Path]:
    return sorted(REPO_ROOT.glob(AGENT_GLOB))


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


# --------------------------------------------------------------------------
# The sweep
# --------------------------------------------------------------------------


def test_the_agent_corpus_is_discovered() -> None:
    """A sweep over an empty glob passes vacuously -- assert it is populated."""
    files = _agent_files()
    assert len(files) >= MIN_EXPECTED_AGENTS, (
        f"Expected at least {MIN_EXPECTED_AGENTS} agent definitions under "
        f"{REPO_ROOT}/{AGENT_GLOB} (A-AUTO-004 counted 38); found "
        f"{len(files)}. If the glob or REPO_ROOT is wrong every other test in "
        f"this module passes without checking anything."
    )


def test_every_agent_frontmatter_parses() -> None:
    """Zero parse failures across the whole corpus.

    This is the assertion that would have caught D-014 at authoring time.
    """
    failures = []
    for path in _agent_files():
        try:
            _parse_frontmatter(_extract_frontmatter(path.read_text(encoding="utf-8")))
        except FrontmatterError as exc:
            failures.append(f"  {_rel(path)}: {exc}")
    assert not failures, (
        f"{len(failures)} agent definition(s) carry frontmatter that YAML "
        f"cannot load as a mapping. The harness drops the ENTIRE mapping on a "
        f"parse error, so every key -- including `model:` -- silently stops "
        f"applying and the agent runs on the inherited session model:\n"
        + "\n".join(failures)
    )


def test_every_agent_declares_a_model_within_the_accepted_set() -> None:
    """Every agent pins a concrete alias from the accepted set (A-AUTO-004)."""
    problems = []
    for path in _agent_files():
        try:
            parsed = _parse_frontmatter(
                _extract_frontmatter(path.read_text(encoding="utf-8"))
            )
        except FrontmatterError:
            continue  # owned by test_every_agent_frontmatter_parses
        model = parsed.get("model")
        if model is None:
            problems.append(f"  {_rel(path)}: declares no `model:` key")
        elif model not in ACCEPTED_MODELS:
            problems.append(f"  {_rel(path)}: model={model!r}")
    assert not problems, (
        f"{len(problems)} agent definition(s) declare a model outside the "
        f"accepted set {sorted(ACCEPTED_MODELS)} (A-014 'Aliases + inherit'). "
        f"This repo pins floating aliases only -- full `claude-*` IDs are legal "
        f"per the sub-agents docs but would freeze an agent at a dated "
        f"snapshot, so add an explicit allowlist entry here if one is ever "
        f"deliberate:\n" + "\n".join(problems)
    )


# --------------------------------------------------------------------------
# The parser bites -- D-014 regression
# --------------------------------------------------------------------------

# The exact scalars that shipped inert, recovered from a902388^. Both are
# `description` values whose unquoted text carries a ": " sequence.
D014_UNQUOTED_SCALARS = [
    pytest.param(
        "F2 INSPECT 8th stream. Code-blind: reads spec only. Derives "
        "hypothesis-jsonschema strategies from TYPE-01 contracts table, "
        "generates and runs failing tests, emits findings to test_observations "
        "channel for ASSAY mediation.",
        id="spec-test-deriver-code-blind",
    ),
    pytest.param(
        "F0.7 phase. Reads spec.md's <Appendix: Interview Transcript> block "
        "FIRST then every casting-{id}-prompt.md.",
        id="intent-carrier-appendix",
    ),
]


def _frontmatter_with_description(description: str) -> str:
    return f"---\nname: probe\ndescription: {description}\nmodel: opus\n---\n"


@pytest.mark.parametrize("description", D014_UNQUOTED_SCALARS)
def test_parser_rejects_the_historical_unquoted_colon_defect(description: str) -> None:
    """The D-014 shape must fail, or this module is decoration.

    Note `model: opus` sits on its own well-formed line in the fixture -- a
    line-anchored regex passes on it. Only a parse catches the defect.
    """
    text = _frontmatter_with_description(description)
    assert re.search(r"^model:\s*opus\s*$", text, re.MULTILINE), (
        "fixture sanity: the old line-anchored regex still matches this file, "
        "which is precisely why regex guards missed D-014"
    )
    with pytest.raises(FrontmatterError, match="mapping values are not allowed here"):
        _parse_frontmatter(_extract_frontmatter(text))


@pytest.mark.parametrize("description", D014_UNQUOTED_SCALARS)
def test_parser_accepts_the_quoted_form_of_the_same_scalar(description: str) -> None:
    """The remedy applied in a902388 -- double-quote the scalar -- must pass."""
    parsed = _parse_frontmatter(
        _extract_frontmatter(_frontmatter_with_description(f'"{description}"'))
    )
    assert parsed["description"] == description
    assert parsed["model"] == "opus", (
        "quoting the description must leave the model pin readable; that pin "
        "being unreachable is what made the repin inert"
    )


def test_parser_rejects_a_missing_frontmatter_block() -> None:
    with pytest.raises(FrontmatterError, match="no leading '---' frontmatter block"):
        _extract_frontmatter("# Just A Heading\n\nbody text\n")


# --------------------------------------------------------------------------
# Keeping the hand-rolled parser honest
# --------------------------------------------------------------------------

_PYYAML_HINT = (
    "pyyaml is not a dependency of this suite; run "
    "`uv run --with pytest --with pyyaml pytest` to exercise this cross-check"
)


def test_strict_parser_agrees_with_pyyaml_across_the_corpus() -> None:
    """Real YAML and the strict parser must reach the same verdict.

    Skipped under the documented invocation. The always-on guard is
    test_every_agent_frontmatter_parses; this one proves that guard is a
    faithful stand-in rather than a regex wearing a parser's hat.
    """
    yaml = pytest.importorskip("yaml", reason=_PYYAML_HINT)
    disagreements = []
    for path in _agent_files():
        block = _extract_frontmatter(path.read_text(encoding="utf-8"))
        try:
            reference = yaml.safe_load(block)
            reference_error = None
        except yaml.YAMLError as exc:
            reference, reference_error = None, str(exc).split("\n")[0]
        try:
            parsed = _parse_frontmatter(block)
            parsed_error = None
        except FrontmatterError as exc:
            parsed, parsed_error = None, str(exc)

        if (reference_error is None) != (parsed_error is None):
            disagreements.append(
                f"  {_rel(path)}: pyyaml={reference_error or 'ok'} / "
                f"strict={parsed_error or 'ok'}"
            )
        elif reference_error is None:
            if set(reference) != set(parsed):
                disagreements.append(
                    f"  {_rel(path)}: key sets differ -- pyyaml="
                    f"{sorted(reference)} strict={sorted(parsed)}"
                )
            elif reference.get("model") != parsed.get("model"):
                disagreements.append(
                    f"  {_rel(path)}: model differs -- pyyaml="
                    f"{reference.get('model')!r} strict={parsed.get('model')!r}"
                )
    assert not disagreements, (
        "the strict parser disagrees with pyyaml. It is allowed to be "
        "STRICTER on constructs this corpus does not use, but never to accept "
        "something YAML rejects or to read a different model value:\n"
        + "\n".join(disagreements)
    )


@pytest.mark.parametrize("description", D014_UNQUOTED_SCALARS)
def test_pyyaml_confirms_the_d014_fixture_is_genuinely_invalid(description: str) -> None:
    """The regression fixture must be real YAML-invalid, not just disliked."""
    yaml = pytest.importorskip("yaml", reason=_PYYAML_HINT)
    block = _extract_frontmatter(_frontmatter_with_description(description))
    with pytest.raises(yaml.YAMLError, match="mapping values are not allowed here"):
        yaml.safe_load(block)
