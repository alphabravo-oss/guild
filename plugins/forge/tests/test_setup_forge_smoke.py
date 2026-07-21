"""Smoke tests for plugins/forge/scripts/setup-forge.sh — R1.75 emission.

Wave 0 (Plan 01-01) baseline: both tests in this file MUST be RED initially.
The setup-forge.sh changes from Plan 02 turn them green.

Each test invokes setup-forge.sh via subprocess (real script, no mocks) and
asserts content of the assembled prompt file.
"""

from __future__ import annotations


# -----------------------------------------------------------------------------
# Test 1: R1.75 IMPLICIT-FACT phase is emitted in the prompt
# -----------------------------------------------------------------------------
def test_implicit_fact_phase_emitted(run_setup_forge):
    result = run_setup_forge("test-feature", "--no-survey")

    # The prompt file must exist and contain the new R1.75 phase content.
    assert result.prompt_path is not None, (
        "Expected setup-forge.sh to emit a prompt file path on stdout; "
        f"stdout:\n{result.process.stdout}\nstderr:\n{result.process.stderr}"
    )
    prompt = result.prompt_text

    assert "PHASE R1.75" in prompt, (
        f"Expected 'PHASE R1.75' in prompt; got prompt of length {len(prompt)}"
    )
    # Case-insensitive check for IMPLICIT-FACT (could be "IMPLICIT-FACT",
    # "IMPLICIT_FACT", or "Implicit Fact" depending on prose).
    lowered = prompt.lower()
    assert "implicit-fact" in lowered or "implicit_fact" in lowered, (
        "Expected an 'IMPLICIT-FACT' (or IMPLICIT_FACT) reference in prompt"
    )
    assert "gap-list" in lowered, (
        "Expected 'gap-list' reference in prompt (scout-then-ask procedure)"
    )


# -----------------------------------------------------------------------------
# Test 2: Closed vocabulary (six categories) appears in the prompt
# -----------------------------------------------------------------------------
def test_closed_vocab_in_prompt(run_setup_forge):
    result = run_setup_forge("test-feature", "--no-survey")

    assert result.prompt_path is not None, (
        "Expected setup-forge.sh to emit a prompt file; "
        f"stdout:\n{result.process.stdout}\nstderr:\n{result.process.stderr}"
    )
    prompt = result.prompt_text

    # All six closed-vocabulary categories must appear (OTHER is the escape
    # hatch and is NOT required by this assertion — only the core six).
    assert "DEPLOYMENT" in prompt, "Expected 'DEPLOYMENT' category in prompt"
    assert "SCALE" in prompt, "Expected 'SCALE' category in prompt"
    assert "RUNTIME" in prompt, "Expected 'RUNTIME' category in prompt"
    assert "FRAMEWORK_VERSION" in prompt, (
        "Expected 'FRAMEWORK_VERSION' category in prompt"
    )
    assert "SECURITY" in prompt, "Expected 'SECURITY' category in prompt"
    assert "NETWORK" in prompt, "Expected 'NETWORK' category in prompt"


# -----------------------------------------------------------------------------
# Test 3: SPEC TEMPLATE emits the three Phase 2 typed-table sections (Plan 02-02)
# -----------------------------------------------------------------------------
def test_spec_template_emits_typed_tables(run_setup_forge):
    """Plan 02-01 RED stub — turns green when Plan 02-02 lands.

    Plan 02-02 extends setup-forge.sh's SPEC TEMPLATE block to:
      * replace ## Global Invariants bullet list with the 5-column
        invariants table (ID | statement | applies-to | violation | citation)
      * add a new ## State Transitions section (6-column table)
      * add a new ## Contracts section (6-column table)
      * include at least one example sentinel row demonstrating the
        documented sentinel-row form

    Each assertion uses plain ``in prompt_text`` matching against the
    assembled prompt setup-forge.sh dumps to stdout (run_setup_forge
    fixture). The header-row format is checked permissively (pipe-stripped,
    optional whitespace) so Plan 02-02 has wiggle room on exact column
    naming; the column COUNT and CORE COLUMN NAMES must match.
    """
    import re

    result = run_setup_forge("test-feature", "--no-survey")

    assert result.prompt_path is not None, (
        "Expected setup-forge.sh to emit a prompt; "
        f"stdout:\n{result.process.stdout}\nstderr:\n{result.process.stderr}"
    )
    prompt = result.prompt_text

    # 1. The three top-level section headings must appear.
    assert "## Global Invariants" in prompt, (
        "Expected '## Global Invariants' heading in SPEC TEMPLATE prompt"
    )
    assert "## State Transitions" in prompt, (
        "Expected NEW '## State Transitions' heading in SPEC TEMPLATE prompt "
        "(Plan 02-02 adds this)"
    )
    assert "## Contracts" in prompt, (
        "Expected NEW '## Contracts' heading in SPEC TEMPLATE prompt "
        "(Plan 02-02 adds this)"
    )

    # 2. Invariants table column-header row — 5 columns: ID, statement,
    #    applies-to, violation, citation. Tolerate optional whitespace and
    #    optional separator characters.
    invariants_header_re = re.compile(
        r"\|\s*ID\s*\|\s*statement\s*\|\s*applies[-_ ]?to\s*\|\s*"
        r"violation\s*\|\s*citation\s*\|",
        re.IGNORECASE,
    )
    assert invariants_header_re.search(prompt), (
        "Expected invariants column header row "
        "'| ID | statement | applies-to | violation | citation |' "
        f"(case-insensitive, optional whitespace) in prompt; "
        f"saw prompt of length {len(prompt)}"
    )

    # 3. State-transitions table column-header row — 6 columns: ID,
    #    from-state, to-state, trigger, guard, citation.
    state_header_re = re.compile(
        r"\|\s*ID\s*\|\s*from[-_ ]?state\s*\|\s*to[-_ ]?state\s*\|\s*"
        r"trigger\s*\|\s*guard\s*\|\s*citation\s*\|",
        re.IGNORECASE,
    )
    assert state_header_re.search(prompt), (
        "Expected state-transitions column header row "
        "'| ID | from-state | to-state | trigger | guard | citation |' "
        f"in prompt"
    )

    # 4. Contracts table column-header row — 6 columns: ID, surface, input,
    #    output, errors, citation.
    contracts_header_re = re.compile(
        r"\|\s*ID\s*\|\s*surface\s*\|\s*input\s*\|\s*output\s*\|\s*"
        r"errors\s*\|\s*citation\s*\|",
        re.IGNORECASE,
    )
    assert contracts_header_re.search(prompt), (
        "Expected contracts column header row "
        "'| ID | surface | input | output | errors | citation |' in prompt"
    )

    # 5. At least one example sentinel row that demonstrates the documented
    #    "None — <reason> [from A-NNN ...]" shape.  This proves Plan 02-02's
    #    template teaches the synthesizer how to write a sentinel.
    sentinel_example_re = re.compile(
        r"\|.*[Nn]one\s*[—\-]\s+.*[Ff]rom\s+A-\d+.*\|"
    )
    assert sentinel_example_re.search(prompt), (
        "Expected at least one example sentinel row matching the documented "
        "form '| ... | None — <reason> ... [from A-NNN ...] | ... |' in "
        "the SPEC TEMPLATE prompt — Plan 02-02 must teach the synthesizer "
        "how to write a sentinel"
    )
