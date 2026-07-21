"""Cross-script alignment test: setup-forge.sh's SPEC TEMPLATE literal
must equal validate-spec.py's LATEST_SPEC_FORMAT_VERSION constant.

RESEARCH.md Pitfall 5: at v2.2 bump time, a maintainer might bump
KNOWN_SPEC_FORMAT_VERSIONS in validate-spec.py but forget to bump the
SPEC TEMPLATE literal in setup-forge.sh (or vice versa). This test
catches the drift before the next forge run.

The two assertions ship as separate tests so a drift-failure points at
the precise broken half (literal-vs-constant alignment, or
literal-vs-allowlist alignment).
"""
from __future__ import annotations

import re
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
SETUP_FORGE = PLUGIN_ROOT / "scripts" / "setup-forge.sh"
VALIDATE_SPEC = PLUGIN_ROOT / "scripts" / "validate-spec.py"

SETUP_FORGE_LITERAL_RE = re.compile(
    r"^spec_format_version:\s*(v\d+\.\d+)\s*$",
    re.MULTILINE,
)
VALIDATE_SPEC_CONSTANT_RE = re.compile(
    r"^LATEST_SPEC_FORMAT_VERSION\s*=\s*\"(v\d+\.\d+)\"",
    re.MULTILINE,
)


def test_setup_forge_template_literal_matches_validate_spec_constant():
    """The single literal in setup-forge.sh SPEC TEMPLATE must equal
    validate-spec.py's LATEST_SPEC_FORMAT_VERSION."""
    setup_forge_text = SETUP_FORGE.read_text(encoding="utf-8")
    validate_spec_text = VALIDATE_SPEC.read_text(encoding="utf-8")

    setup_match = SETUP_FORGE_LITERAL_RE.search(setup_forge_text)
    assert setup_match, (
        f"setup-forge.sh does not contain a 'spec_format_version: vX.Y' "
        f"line in its SPEC TEMPLATE block. Plan 03-02 must emit this "
        f"frontmatter prelude. Search pattern: "
        f"{SETUP_FORGE_LITERAL_RE.pattern!r}"
    )
    validate_match = VALIDATE_SPEC_CONSTANT_RE.search(validate_spec_text)
    assert validate_match, (
        f"validate-spec.py does not declare LATEST_SPEC_FORMAT_VERSION "
        f"as a top-level constant. Plan 03-02 must add this constant."
    )
    setup_literal = setup_match.group(1)
    validate_constant = validate_match.group(1)
    assert setup_literal == validate_constant, (
        f"Cross-script alignment failed: setup-forge.sh emits "
        f"spec_format_version={setup_literal!r} but validate-spec.py "
        f"declares LATEST_SPEC_FORMAT_VERSION={validate_constant!r}. "
        f"At a version bump, both must move together. See "
        f"RESEARCH.md Pitfall 5 for context."
    )


def test_setup_forge_template_literal_in_known_allowlist():
    """The literal emitted by setup-forge.sh MUST be in the validator's
    KNOWN_SPEC_FORMAT_VERSIONS. Defends against an accidental bump of
    the literal without a corresponding allowlist edit."""
    setup_forge_text = SETUP_FORGE.read_text(encoding="utf-8")
    validate_spec_text = VALIDATE_SPEC.read_text(encoding="utf-8")
    setup_match = SETUP_FORGE_LITERAL_RE.search(setup_forge_text)
    assert setup_match
    setup_literal = setup_match.group(1)
    # Find the allowlist tuple text, e.g., ("v2.0", "v2.1")
    allowlist_match = re.search(
        r"KNOWN_SPEC_FORMAT_VERSIONS[^=]*=\s*\(([^)]*)\)",
        validate_spec_text,
    )
    assert allowlist_match, (
        "KNOWN_SPEC_FORMAT_VERSIONS not found in validate-spec.py"
    )
    allowlist_body = allowlist_match.group(1)
    assert (
        f'"{setup_literal}"' in allowlist_body
        or f"'{setup_literal}'" in allowlist_body
    ), (
        f"setup-forge.sh emits spec_format_version={setup_literal!r} "
        f"but this value is not in KNOWN_SPEC_FORMAT_VERSIONS "
        f"({allowlist_body.strip()}). Add it to the allowlist."
    )
