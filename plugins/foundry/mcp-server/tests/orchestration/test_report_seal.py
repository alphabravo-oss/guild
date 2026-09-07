"""The REPORT.md seal and the lead prose it carries.

Carved from `tests/test_orchestrator_gates.py` (fallout FR-005 / GI-026 /
AC-014 / OT-016): one test module per shipped orchestration module, landed in
the same casting as the source move so no pin is ever left pointing at a module
that no longer exists.
"""
from __future__ import annotations

import inspect

import pytest

from foundry_mcp.schemas import vocab

# fallout FR-004 / AC-013 — THE MODULE OBJECTS, UNDER UNDERSCORE ALIASES.
#
# `streams`, `spend`, `width`, `gates`, `directives` and `teams` are all LOCAL
# variable names somewhere in this suite, and a local rebinding shadows a
# module for the rest of its function. The aliases are what `ORCHESTRATION`,
# `owning_module` and every `monkeypatch.setattr` resolve through; individual
# SYMBOLS are imported by name below, which is how the carved modules read.

# fallout AC-014 — THE TWO SIBLING SUITES THE CARVE MUST KEEP REACHING.
#
# `test_observations` owns the never-demote corpus and `test_spawn_progress`
# owns the shipped-source-tree derivation. Both are imported rather than copied,
# for the reason the monolith imported them: a parity test that owned its own
# copy of the corpus would keep passing while the two corpora drifted, and two
# scans over "the shipped source" must not be able to disagree about what that
# is. If either renames a symbol the ImportError says so by name, which is the
# loud failure rather than the silent one.

# fallout FR-004 / AC-014 (D-183) — THE ROSTER AND ITS HELPERS COME FROM
# `tests/orchestration/_env.py`, WHICH IS THE ONE PLACE THEY ARE STATED.
#
# This module carried its own byte-identical copy of a hand-typed thirteen-tuple
# and of `orchestration_source`, `owning_module`, `patch_everywhere` and
# `orchestration_has`. Fourteen copies of one roster is fourteen places to
# forget a module, and `keyfiles.py` — shipped in cycle 5 — was forgotten in
# every one of them: `owning_module` answered the IMPORTING module for
# `covers_path` and raised for `owning_entries`, and `patch_everywhere` could
# not reach a binding inside it. The roster is derived from the package
# directory now, so there is one of it and it cannot go stale.

from tests.orchestration._env import (  # noqa: F401
    _arm_ordering_token,
    _generate_report,
    run_env,
)

from foundry_mcp.tools.orchestration.halt import (  # noqa: F401
    _seal_halted,
)

from foundry_mcp.tools.orchestration.report_seal import (
    _generate_report,  # noqa: F401
    _LEAD_NOTES_HEADING,
    _lead_prose_clause,
    _seal_lead_prose,
    _sealed_report_sentence,
)

from foundry_mcp.tools.orchestration.spend import (  # noqa: F401
    foundry_record_spend,
)

from foundry_mcp.tools.orchestration.transitions import (  # noqa: F401
    foundry_mark_phase_complete,
)

from tests.orchestration._env import (  # noqa: F401
    _LEAD_HEADING,
    _TERMINAL_DOORS,
    _append_lead_prose,
    _arrange_terminal_door,
    _assert_lead_prose_survived,
)




def _assert_no_generated_row_was_duplicated(text: str) -> None:
    """D-228 / D-230's observed shape: one question, two contradictory rows.

    The sealed document carried the STALE `| run | total | ... |` row from the
    previous generation directly beside the fresh one, and the stale
    `| Tokens | ... |` of the baseline comparison beside its fresh one. Both are
    rows the generator emits exactly once, so counting them is the assertion:
    a duplicate can only have arrived by the seal re-emitting generated content
    as lead prose.
    """
    assert text.count("| run | total |") == 1, text[-3000:]
    # By WHOLE LINE, because "| Tokens |" is also the spend table's header cell
    # and a substring count would pass on a duplicated row.
    for row in ("| Tokens |", "| GRIND cycles |"):
        matched = [line for line in text.splitlines() if line.startswith(row)]
        assert len(matched) == 1, (row, matched)
    # Every generated section appears exactly once, whatever else is below.
    assert text.count("\n## ") == len(vocab.REPORT_REQUIRED_SECTIONS) + (
        2 if _LEAD_NOTES_HEADING in text else 0
    ), [line for line in text.splitlines() if line.startswith("## ")]




@pytest.mark.parametrize("door", _TERMINAL_DOORS)
def test_a_ledger_that_moved_under_the_report_is_not_prose_the_lead_appended(
    run_env, door
):
    """D-228 / D-230 — GI-006 / AC-036 / FR-023 / CT-014, driven at every door.

    THE DEFECT. `_lead_only_lines` kept the `insert` AND `replace` opcodes of a
    line-granular `difflib` comparison between the pre-existing section body and
    the freshly generated one. A generated row whose VALUE moved between the two
    generations lands in a `replace` opcode, so the OLD row was kept and
    re-emitted below the fresh section as the lead's content. Driven at the real
    terminal door with ZERO prose appended and ONE `Foundry-Spend` recorded
    between the two generations: the transition returned `lead_prose_lines: 11`
    and said "11 line(s) of prose you appended were carried onto it (GI-006)",
    and the sealed REPORT.md carried a stale `| GRIND cycles | 22 |  | 12 | 2 |`
    directly below the fresh `| GRIND cycles | 22 |  | 12 | 1 |`. The run's
    final artifact ended with two contradictory values for one question and the
    operator was told they had authored the contradiction.

    ARM (a) OF THE RULING'S REGRESSION TEST: zero prose plus one spend between
    generations — carried count 0, no trailing section, no stale rows anywhere,
    and no sentence claiming the operator appended anything.
    """
    project_root, fdir = run_env
    _arrange_terminal_door(fdir, door)
    _generate_report(project_root, fdir)

    # THE LEDGER MOVES, AND NOBODY TYPES A WORD. This is the whole trigger: one
    # more spend record between the generation and the seal changes the tokens
    # cell, the totals row and the baseline comparison.
    spend = foundry_record_spend(
        agent="casting-3", phase="F3", tokens=4242, duration_ms=60_000,
        project_root=project_root,
    )
    assert spend["ok"] is True, spend

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete(door, project_root)

    assert result["ok"] is True, result
    assert result["report_generated"] is True, result
    assert result["lead_prose_lines"] == 0, result
    assert not result["lead_prose_error"], result
    assert "prose you appended" not in result["message"], result["message"]

    text = (fdir / "REPORT.md").read_text(encoding="utf-8")
    assert _LEAD_NOTES_HEADING not in text, text[-3000:]
    _assert_no_generated_row_was_duplicated(text)
    # The FRESH number is the one on the document, and it is there once.
    assert text.count("| run | total | 4242 |") == 1, text[-3000:]




@pytest.mark.parametrize("door", _TERMINAL_DOORS)
def test_the_seal_carries_the_lead_block_and_nothing_generated_with_it(
    run_env, door
):
    """ARM (b) OF THE RULING'S REGRESSION TEST — D-228 / D-230 / GI-006.

    A `## Lead notes` section with a sentinel, AND a ledger that moved under the
    document: exactly the lead's content in the single trailing section, every
    generated section present exactly once with fresh values, and nothing
    generated re-emitted as prose. The count the message names is the lead's
    line count, not a diff's.
    """
    project_root, fdir = run_env
    _arrange_terminal_door(fdir, door)
    _generate_report(project_root, fdir)
    _append_lead_prose(fdir)

    spend = foundry_record_spend(
        agent="casting-3", phase="F3", tokens=4242, duration_ms=60_000,
        project_root=project_root,
    )
    assert spend["ok"] is True, spend

    _arm_ordering_token(fdir)
    result = foundry_mark_phase_complete(door, project_root)

    assert result["ok"] is True, result
    assert result["report_generated"] is True, result
    # The lead wrote a heading, a blank and a sentinel — carried VERBATIM, so
    # the blank between them is one of the three lines the count names.
    assert result["lead_prose_lines"] == 3, result
    assert not result["lead_prose_error"], result
    # D-233 — AND `grind_start` IS NO LONGER EXEMPT FROM THIS.
    #
    # This assertion used to be fenced behind `if door != "grind_start"`,
    # excused as "the cap has its own message and publishes the same count as a
    # result field". It publishes the field and `display.py` renders a phase
    # result's `message` and nothing else, so the cap door SEALED the lead's
    # prose and told the operator nothing — D-224's silence verbatim, at the
    # third terminal door. `_lead_prose_clause` is the one spelling all three
    # now say, so the exemption is closed by the code rather than by the test.
    assert _LEAD_NOTES_HEADING in result["message"], (door, result["message"])
    assert "3 line(s) of prose you appended" in result["message"], (
        door, result["message"]
    )

    text = _assert_lead_prose_survived(fdir)
    _assert_no_generated_row_was_duplicated(text)
    assert text.count("| run | total | 4242 |") == 1, text[-3000:]
    # The carried block is BELOW every generated section, not interleaved.
    headings = [line for line in text.splitlines() if line.startswith("## ")]
    assert headings[-2:] == [_LEAD_NOTES_HEADING, _LEAD_HEADING], headings




def test_the_seal_takes_its_skeleton_from_the_document_just_generated(run_env):
    """The branch the doors cannot reach, driven on the helper itself.

    `_carried_lead_prose` walks the document the LEAD edited, so "what if a
    generated heading is not in it" has to have an answer. The answer is that
    the skeleton comes from the FRESHLY GENERATED document — not from
    `foundry_report._SECTION_TITLES`, which is a sibling casting's private name
    this module would then have to keep in step. Asserted directly because
    `_done_preconditions` refuses that document at the door (see the test
    above), so the door can never exercise it.

    D-228: and the STALE ALPHA ROW IS GONE. It used to be carried "because the
    bias is towards preserving", which is what published a generated row as the
    operator's own words. A row under a generated heading is generated by
    construction; the seam is the heading and nothing crosses it.
    """
    generated = (
        "# Foundry run report — r\n"
        "\n"
        "Generated 2020-01-01T00:00:00+00:00 by Foundry-Report. Every section "
        "below is generated from the run's own ledgers (GI-006).\n"
        "\n"
        "## Alpha\n"
        "\n"
        "fresh alpha row\n"
        "\n"
        "## Beta\n"
        "\n"
        "fresh beta row\n"
    )
    edited = (
        "# Foundry run report — r\n"
        "\n"
        "Generated 2019-01-01T00:00:00+00:00 by Foundry-Report. Every section "
        "below is generated from the run's own ledgers (GI-006).\n"
        "\n"
        "lead prose above the first section\n"
        "\n"
        "## Alpha\n"
        "\n"
        "stale alpha row\n"
        "\n"
        "## Lead notes\n"
        "\n"
        "lead prose under the lead's own heading\n"
    )

    sealed, carried = _seal_lead_prose(edited, generated)

    assert carried > 0
    headings = [line for line in sealed.splitlines() if line.startswith("## ")]
    # Beta was absent from the edited document and comes back; the carried block
    # is one trailing section; neither generated heading is emitted twice.
    assert headings == [
        "## Alpha", "## Beta", _LEAD_NOTES_HEADING, "## Lead notes",
    ], headings
    for kept in (
        "lead prose above the first section",
        "lead prose under the lead's own heading",
    ):
        assert kept in sealed, (kept, sealed)
    assert "fresh alpha row" in sealed and "fresh beta row" in sealed
    # THE ROW UNDER A GENERATED HEADING IS GENERATED, and the stale copy of it
    # is not republished as the lead's.
    assert "stale alpha row" not in sealed, sealed
    # ...and the old banner is NOT carried either, because it is regenerated.
    assert "2019-01-01" not in sealed, sealed




def test_the_seal_invents_nothing_when_a_generated_value_moved(run_env):
    """D-228's minimal isolated reproduction, as a unit assertion.

    `_merge_lead_prose(old, new)` where old and new differed only in ONE table
    row returned `preserved=1` and a merged document carrying BOTH rows. The
    same inputs must now carry nothing: a document that is purely generated
    seals to itself, whatever moved inside it.

    Asserted on the helper as well as through the doors because this is the
    property that stops the seal GROWING the report: every terminal transition
    runs it, so a seal that preserved even one generated line per pass would
    accumulate a duplicate section over a long run.
    """
    banner = (
        "# Foundry run report — r\n"
        "\n"
        "Generated {stamp} by Foundry-Report.\n"
        "\n"
        "## Alpha\n"
        "\n"
        "| a | {value} |\n"
    )
    old = banner.format(stamp="2019-01-01T00:00:00+00:00", value=1)
    new = banner.format(stamp="2020-01-01T00:00:00+00:00", value=2)

    sealed, carried = _seal_lead_prose(old, new)
    assert carried == 0
    assert sealed == new
    assert "| a | 1 |" not in sealed

    # And the identity case, which is the same property with nothing moving.
    sealed, carried = _seal_lead_prose(new, new)
    assert carried == 0
    assert sealed == new




def test_sealing_twice_does_not_wrap_the_wrapper(run_env):
    """The seal is idempotent over its own output.

    Two terminal transitions can both run on one run — a capped GRIND opens
    HALTED, and a resumed run can still reach `done` — so the document the seal
    reads may be a document the seal wrote. Its own section is UNWRAPPED and
    re-wrapped once rather than nested, so the carried block does not gain a
    heading per pass.
    """
    generated = (
        "# Foundry run report — r\n"
        "\n"
        "Generated 2020-01-01T00:00:00+00:00 by Foundry-Report.\n"
        "\n"
        "## Alpha\n"
        "\n"
        "fresh alpha row\n"
    )
    edited = generated.rstrip() + "\n\n## Lead notes\n\nsentinel\n"

    once, first = _seal_lead_prose(edited, generated)
    twice, second = _seal_lead_prose(once, generated)

    assert first == second == 3, (first, second)
    assert once == twice, (once, twice)
    assert twice.count(_LEAD_NOTES_HEADING) == 1, twice
    assert twice.count("sentinel") == 1, twice




def test_the_three_terminal_doors_share_one_spelling_of_the_prose_clauses(run_env):
    """D-233's design invariant, asserted on the code rather than on a message.

    `_lead_prose_clause` exists so that `done`, `nyquist_done` and the cap
    cannot come apart on what they say about the lead's own additions. A future
    author who re-inlines either clause at one door re-creates the defect, and
    the two-of-three coverage that hid it for a cycle is exactly why the check
    is on the shared name.
    """
    import inspect

    # fallout FR-046: the cap and the lead's ruling both seal through
    # `_seal_halted`, which is where the clause now lives — one writer, so the
    # two endings cannot come apart on what they say about the lead's prose
    # either. `_halt_if_capped` is the ACTION the cap fact licenses and states
    # no sentence of its own.
    halt_src = inspect.getsource(_seal_halted)
    sentence_src = inspect.getsource(_sealed_report_sentence)

    for name, src in (("_seal_halted", halt_src),
                      ("_sealed_report_sentence", sentence_src)):
        assert "_lead_prose_clause(" in src, name
        # Neither door re-spells the clauses it delegates.
        assert "WARNING: " not in src, name
        assert "prose you appended" not in src, name

    # And the helper answers nothing at all when there is nothing to report, so
    # a run with no prose and no failure gets no sentence about prose.
    assert _lead_prose_clause(
        {"lead_prose_lines": 0, "lead_prose_error": ""}
    ) == ""
    assert "WARNING: disk full" in _lead_prose_clause(
        {"lead_prose_lines": 0, "lead_prose_error": "disk full"}
    )
    assert "4 line(s) of prose you appended" in _lead_prose_clause(
        {"lead_prose_lines": 4, "lead_prose_error": ""}
    )
