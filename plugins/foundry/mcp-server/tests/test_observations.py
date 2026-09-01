"""Casting 3 — the observation/defect split, the never-demote tripwire, the
reconciled Foundry-Defect vocabulary, and defect-id uniqueness under
concurrency.

One regression test per acceptance criterion:

  AC-001 / OT-001  comment-prose filed as a defect is REFUSED naming the class
                   and the legal set, the same finding is ACCEPTED into
                   observations.json, and defects.json never contains it.
  AC-002 / OT-002  a denylisted finding filed as an observation is REJECTED and
                   the audit tripwire fires durably (observations.json +
                   forge-log.md); a security-property claim about a comment is
                   a DEFECT and files successfully.
  AC-004           a fresh run carries the seeded F0 ruling (see also
                   test_foundry_init.py, which asserts the parse).
  AC-019 / OT-008  defect_type PARTIAL is accepted and stored verbatim, with
                   source preserved verbatim.
  AC-025           concurrently filed defects get unique ids and BOTH survive.
  CT-002           unknown source / defect_type are rejected server-side with a
                   named error; source is never coerced onto "trace".
  FR-023           the ledger is typed, per-run, and never mixed into
                   defects.json.

The safety property this file guards hardest is the one that is NOT stated as
an AC but is the whole point of A-004: the split must never weaken the defect
standard. ``test_undeclared_subject_is_never_refused`` and
``test_denylist_outranks_observation_class`` are the two tests that fail if a
future change lets a real defect slip into the non-blocking channel.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import (
    NEVER_DEMOTE_CLASSES,
    OBSERVATION_CLASSES,
)
from foundry_mcp.tools.foundry import (
    allocate_record_id,
    foundry_add_defect,
    foundry_add_observation,
    foundry_init,
    foundry_query_defects,
    foundry_query_observations,
    ledger_transaction,
)
from foundry_mcp.tools.foundry_state import clear_active_run

# Descriptions engineered to match exactly one vocab observation class.
DRIFT = "The cite's line number is stale after the refactor."
COUNT = "The docstring says 8 items but there are 9 now."
DIRECTION = "The comment says the helper is defined above, but it moved."
ENUMERATION = "The comment's list of streams omits flow_trace."

# Matches DIRECTION_WORD as prose, but is a real behavioural defect. The gate
# must never refuse this one on description alone.
REAL_DEFECT_PROSE = "The handler below returns the wrong status code."

SECURITY = "The comment claims the handler validates the CSRF token; it does not."


@pytest.fixture(autouse=True)
def _isolate_active_run():
    """Reset the module-level active run so foundry_init's side effect never
    leaks between tests (or into the real repo)."""
    clear_active_run()
    yield
    clear_active_run()


@pytest.fixture
def run(tmp_path: Path) -> Path:
    """An initialized foundry run rooted at tmp_path. Returns the run dir."""
    result = foundry_init(project_root=str(tmp_path))
    return Path(result["foundry_dir"])


def _defects(fdir: Path) -> list[dict]:
    return json.loads((fdir / "defects.json").read_text(encoding="utf-8"))["defects"]


def _observations(fdir: Path) -> dict:
    return json.loads((fdir / "observations.json").read_text(encoding="utf-8"))


# --- AC-001 / OT-001 --------------------------------------------------------
@pytest.mark.parametrize(
    "description,expected_class",
    [
        (DRIFT, "LINE_DRIFT_CITE"),
        (COUNT, "PROSE_COUNT"),
        (DIRECTION, "DIRECTION_WORD"),
        (ENUMERATION, "ENUMERATION"),
    ],
)
def test_comment_prose_filed_as_defect_is_refused(
    run: Path, tmp_path: Path, description: str, expected_class: str
) -> None:
    """AC-001 — each of the four comment-prose classes is refused by
    Foundry-Defect, with an error naming the offending class AND the legal
    set."""
    result = foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="WRONG",
        description=description,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert "error" in result, result
    assert result["refused_class"] == expected_class
    assert expected_class in result["error"]
    # The legal set is named, per the named-refusal house rule.
    for name in OBSERVATION_CLASSES:
        assert name in result["error"], f"{name} missing from legal set"
    # The hint names the action.
    assert "observations ledger" in result["hint"]
    # OT-001 — it never reaches defects.json.
    assert _defects(run) == []


def test_refused_finding_is_accepted_as_an_observation(
    run: Path, tmp_path: Path
) -> None:
    """AC-001 — the SAME finding the defect ledger refused is recordable in the
    observations ledger, and lands there with its class."""
    refusal = foundry_add_defect(
        cycle=2,
        source="prove",
        defect_type="WRONG",
        description=DRIFT,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert refusal["refused_class"] == "LINE_DRIFT_CITE"

    accepted = foundry_add_observation(
        cycle=2,
        source="prove",
        description=DRIFT,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert accepted["observation_id"] == "O-001", accepted
    assert accepted["classification"] == "LINE_DRIFT_CITE"

    ledger = _observations(run)
    assert len(ledger["observations"]) == 1
    record = ledger["observations"][0]
    assert record["description"] == DRIFT
    assert record["classification"] == "LINE_DRIFT_CITE"
    assert record["source"] == "prove"
    assert record["cycle"] == 2
    assert record["created_at"]
    # FR-023 / OT-001 — never mixed into defects.json.
    assert _defects(run) == []


def test_observation_is_mirrored_into_forge_log(run: Path, tmp_path: Path) -> None:
    """Machine-readable JSON *and* a human-readable markdown mirror."""
    foundry_add_observation(
        cycle=3,
        source="trace",
        description=ENUMERATION,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    log = (run / "forge-log.md").read_text(encoding="utf-8")
    assert "O-001" in log
    assert "ENUMERATION" in log


# --- The never-weaken guarantee ---------------------------------------------
def test_undeclared_subject_is_never_refused(run: Path, tmp_path: Path) -> None:
    """A finding whose subject was NOT declared a comment is filed as a defect,
    even when its prose matches an observation regex.

    vocab.py: "target_kind ... its absence is a caller bug, not a licence to
    demote." Refusing a defect IS a demotion. Without this rule the loose
    DIRECTION_WORD pattern would swallow a real behavioural defect whose
    description merely contains "below" near "wrong"."""
    result = foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="BROKEN",
        description=REAL_DEFECT_PROSE,
        project_root=str(tmp_path),
    )
    assert "error" not in result, result
    assert result["defect_id"] == "D-001"
    assert _defects(run)[0]["description"] == REAL_DEFECT_PROSE


def test_same_prose_is_refused_once_declared_a_comment(
    run: Path, tmp_path: Path
) -> None:
    """The mirror of the test above: the identical description IS refused once
    the caller declares the subject is a comment. Declaration is the whole
    discriminator."""
    result = foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="BROKEN",
        description=REAL_DEFECT_PROSE,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert result["refused_class"] == "DIRECTION_WORD", result


@pytest.mark.parametrize(
    "kwargs",
    [
        {"description": SECURITY},  # SECURITY_PROPERTY_CLAIM
        {"description": DIRECTION, "spec_ref": "AC-007"},  # SPEC_REQUIRED_...
    ],
)
def test_denylist_outranks_observation_class(
    run: Path, tmp_path: Path, kwargs: dict
) -> None:
    """OT-002 / AC-002 — a finding matching BOTH a denylist entry and an
    observation class is a DEFECT. The denylist outranks the observation
    class, so the filing succeeds rather than being refused."""
    result = foundry_add_defect(
        cycle=1,
        source="assay",
        defect_type="WRONG",
        target_kind="comment",
        project_root=str(tmp_path),
        **kwargs,
    )
    assert "error" not in result, result
    assert result["defect_id"] == "D-001"


# --- AC-002 / OT-002 --------------------------------------------------------
def test_security_claim_cannot_be_demoted_and_fires_tripwire(
    run: Path, tmp_path: Path
) -> None:
    """AC-002 — a security-property claim can never be recorded as an
    observation; the attempt is rejected and the audit tripwire fires."""
    result = foundry_add_observation(
        cycle=4,
        source="assay",
        description=SECURITY,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert "error" in result, result
    assert result["denylist_class"] == "SECURITY_PROPERTY_CLAIM"
    assert "SECURITY_PROPERTY_CLAIM" in result["error"]
    for name in NEVER_DEMOTE_CLASSES:
        assert name in result["error"], f"{name} missing from denylist set"
    assert "Foundry-Defect" in result["hint"]

    # The signal is DURABLE — readable by the lead and by a validator.
    ledger = _observations(run)
    assert ledger["observations"] == []
    assert len(ledger["tripwire"]) == 1
    fired = ledger["tripwire"][0]
    assert fired["denylist_class"] == "SECURITY_PROPERTY_CLAIM"
    assert fired["description"] == SECURITY
    assert fired["source"] == "assay"
    assert fired["fired_at"]

    log = (run / "forge-log.md").read_text(encoding="utf-8")
    assert "TRIPWIRE" in log
    assert "SECURITY_PROPERTY_CLAIM" in log


def test_spec_ref_makes_a_finding_undemotable(run: Path, tmp_path: Path) -> None:
    """AC-002 — a spec-required-behaviour claim can never be an observation. A
    non-empty spec_ref IS such a claim, so citing a requirement is by itself
    enough to keep a finding in the blocking channel."""
    result = foundry_add_observation(
        cycle=1,
        source="prove",
        description=DRIFT,
        spec_ref="AC-005",
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert result["denylist_class"] == "SPEC_REQUIRED_BEHAVIOUR_CLAIM", result
    assert len(_observations(run)["tripwire"]) == 1


def test_non_comment_subject_cannot_be_an_observation(
    run: Path, tmp_path: Path
) -> None:
    """AC-002 — "anything non-comment" can never be an observation, and an
    UNDECLARED subject cannot be shown to be a comment either. Both are
    rejected under the NON_COMMENT entry."""
    for target_kind in ("function", ""):
        result = foundry_add_observation(
            cycle=1,
            source="trace",
            description=DRIFT,
            target_kind=target_kind,
            project_root=str(tmp_path),
        )
        assert result["denylist_class"] == "NON_COMMENT", (target_kind, result)
    assert len(_observations(run)["tripwire"]) == 2


def test_non_comment_prose_is_not_recordable_as_an_observation(
    run: Path, tmp_path: Path
) -> None:
    """A finding that matches no comment-prose class is refused rather than
    silently filed under an invented classification.

    The description is deliberately free of security vocabulary: the denylist
    patterns are broad on purpose, so a phrase like "bad password" would be
    caught by SECURITY_PROPERTY_CLAIM first and exercise the wrong branch."""
    result = foundry_add_observation(
        cycle=1,
        source="trace",
        description="The endpoint returns 200 when it should return 404.",
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert "error" in result, result
    assert "No comment-prose observation class matches" in result["error"]
    assert _observations(run)["observations"] == []


# --- CT-002 / AC-019 / OT-008 -----------------------------------------------
def test_partial_defect_type_is_accepted_and_stored_verbatim(
    run: Path, tmp_path: Path
) -> None:
    """AC-019 / OT-008 — Foundry-Defect accepts PARTIAL and stores it
    verbatim."""
    result = foundry_add_defect(
        cycle=1,
        source="flow_trace",
        defect_type="PARTIAL",
        description="Packet produces only half its declared outputs.",
        project_root=str(tmp_path),
    )
    assert "error" not in result, result
    record = _defects(run)[0]
    assert record["type"] == "PARTIAL"
    # AC-019 — source attribution is preserved verbatim, not coerced.
    assert record["source"] == "flow_trace"


def test_architectural_placement_type_is_stored_verbatim(
    run: Path, tmp_path: Path
) -> None:
    """Both live spellings are members and neither is folded onto the other —
    folding would be a coercion, which CT-002 forbids on this surface."""
    for i, spelling in enumerate(("MISPLACED", "ARCHITECTURAL_PLACEMENT")):
        foundry_add_defect(
            cycle=1,
            source="trace",
            defect_type=spelling,
            description=f"placement finding {i}",
            project_root=str(tmp_path),
        )
    assert [d["type"] for d in _defects(run)] == [
        "MISPLACED",
        "ARCHITECTURAL_PLACEMENT",
    ]


def test_unknown_source_is_rejected_without_coercion(
    run: Path, tmp_path: Path
) -> None:
    """CT-002 — server-side rejection of an unknown source, naming the legal
    set. Emphatically NOT coerced onto "trace", which is what the old sync
    path did and what made a finding show up under a stream that never filed
    it."""
    result = foundry_add_defect(
        cycle=1,
        source="bogus_stream",
        defect_type="WRONG",
        description="anything",
        project_root=str(tmp_path),
    )
    assert "error" in result, result
    assert "bogus_stream" in result["error"]
    assert "trace" in result["error"]  # named as a legal value, not substituted
    assert _defects(run) == [], "an unknown source must file nothing at all"


def test_unknown_defect_type_is_rejected(run: Path, tmp_path: Path) -> None:
    """CT-002 — unknown defect_type is refused by name."""
    result = foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="COSMETIC",
        description="anything",
        project_root=str(tmp_path),
    )
    assert "error" in result, result
    assert "COSMETIC" in result["error"]
    assert "PARTIAL" in result["error"]
    assert _defects(run) == []


def test_defect_class_field_is_persisted_under_the_class_key(
    run: Path, tmp_path: Path
) -> None:
    """The optional root-cause field is named exactly "class" — escalation
    keys on it."""
    foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="MISSING",
        description="third instance of the same root cause",
        defect_class="UNWIRED_DISPATCH",
        project_root=str(tmp_path),
    )
    assert _defects(run)[0]["class"] == "UNWIRED_DISPATCH"


# --- AC-025 / FR-020 --------------------------------------------------------
def test_concurrent_defects_get_unique_ids_and_all_survive(
    run: Path, tmp_path: Path
) -> None:
    """AC-025 — the positional ``len(defects) + 1`` allocation let two
    simultaneous filings compute the same id, and the second .tmp rename
    discarded the first record entirely. Both properties are asserted: ids are
    unique AND no record is lost."""
    filings = 24
    barrier = threading.Barrier(filings)
    results: list[dict] = []
    lock = threading.Lock()

    def _file(n: int) -> None:
        barrier.wait(timeout=30)
        r = foundry_add_defect(
            cycle=1,
            source="trace",
            defect_type="MISSING",
            description=f"concurrent finding {n}",
            project_root=str(tmp_path),
        )
        with lock:
            results.append(r)

    threads = [threading.Thread(target=_file, args=(n,)) for n in range(filings)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(results) == filings
    ids = [r["defect_id"] for r in results]
    assert len(set(ids)) == filings, f"duplicate ids issued: {sorted(ids)}"

    persisted = _defects(run)
    assert len(persisted) == filings, "a concurrent filing was lost"
    assert len({d["id"] for d in persisted}) == filings
    # Every description survived — proves no read-modify-write was clobbered.
    assert {d["description"] for d in persisted} == {
        f"concurrent finding {n}" for n in range(filings)
    }


def test_observations_and_defects_are_separate_ledgers(
    run: Path, tmp_path: Path
) -> None:
    """FR-023 — the separation is the locked part: observations are typed,
    persisted per run, and never mixed into defects.json."""
    foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="MISSING",
        description="a genuinely missing symbol",
        project_root=str(tmp_path),
    )
    foundry_add_observation(
        cycle=1,
        source="trace",
        description=COUNT,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    defects = _defects(run)
    assert len(defects) == 1
    assert defects[0]["id"] == "D-001"
    assert all("classification" not in d for d in defects)

    ledger = _observations(run)
    assert len(ledger["observations"]) == 1
    assert ledger["observations"][0]["id"] == "O-001"
    # Distinct id namespaces, so a record can never be mistaken for the other.
    assert {d["id"] for d in defects}.isdisjoint(
        {o["id"] for o in ledger["observations"]}
    )


# --- allocate_record_id (the exported allocator) -----------------------------
def test_allocate_record_id_is_max_plus_one_not_positional() -> None:
    """The allocator must not re-issue a live id after a removal, which is
    exactly what ``len(records) + 1`` did."""
    records = [{"id": "D-001"}, {"id": "D-002"}, {"id": "D-003"}]
    assert allocate_record_id(records, "D") == "D-004"
    # A gap in the middle: positional would return D-003 and collide.
    del records[1]
    assert allocate_record_id(records, "D") == "D-004"


def test_allocate_record_id_has_no_999_ceiling() -> None:
    """The width is a minimum, so the sequence continues past the format's
    informal cap instead of wrapping onto a live id."""
    assert allocate_record_id([{"id": "D-999"}], "D") == "D-1000"


def test_allocate_record_id_is_total_over_junk() -> None:
    """Malformed, missing, and foreign-prefix ids contribute nothing rather
    than raising — a corrupt ledger must not break filing."""
    records = [
        {"id": "D-001"},
        {"id": "not-an-id"},
        {"id": None},
        {},
        {"id": "O-007"},  # different namespace
        "not even a dict",
    ]
    assert allocate_record_id(records, "D") == "D-002"
    assert allocate_record_id(records, "O") == "O-008"


def test_allocate_record_id_starts_at_one_for_an_empty_ledger() -> None:
    assert allocate_record_id([], "D") == "D-001"
    assert allocate_record_id([], "O") == "O-001"


# --- ledger_transaction (the exported write discipline) ----------------------
def test_ledger_transaction_writes_through_tmp_rename(
    run: Path, tmp_path: Path
) -> None:
    """The locked read-modify-write persists via the atomic pair and leaves no
    .tmp behind."""
    path = run / "defects.json"
    with ledger_transaction(path, "defects") as defects:
        defects.append({"id": "D-001", "status": "open"})
    assert _defects(run) == [{"id": "D-001", "status": "open"}]
    assert not (run / "defects.tmp").exists()


def test_ledger_transaction_does_not_write_on_exception(
    run: Path, tmp_path: Path
) -> None:
    """An exception inside the block leaves the ledger untouched, so a failed
    classification can never persist a half-updated record."""
    path = run / "defects.json"
    with pytest.raises(RuntimeError):
        with ledger_transaction(path, "defects") as defects:
            defects.append({"id": "D-001"})
            raise RuntimeError("boom")
    assert _defects(run) == []


def test_ledger_transaction_can_mutate_existing_records(
    run: Path, tmp_path: Path
) -> None:
    """The reopen-a-regression pass mutates records already in the ledger and
    appends new ones in ONE critical section — an append-only helper could not
    make that atomic, which is why the exported surface is a transaction."""
    foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="MISSING",
        description="original",
        project_root=str(tmp_path),
    )
    path = run / "defects.json"
    with ledger_transaction(path, "defects") as defects:
        defects[0]["status"] = "fixed"
        defects.append(
            {"id": allocate_record_id(defects, "D"), "status": "open"}
        )
    persisted = _defects(run)
    assert persisted[0]["status"] == "fixed"
    assert persisted[1]["id"] == "D-002"


# --- query surface (FR-023) --------------------------------------------------
def test_query_observations_filters_and_summarizes(
    run: Path, tmp_path: Path
) -> None:
    foundry_add_observation(
        cycle=1, source="trace", description=DRIFT,
        target_kind="comment", project_root=str(tmp_path),
    )
    foundry_add_observation(
        cycle=2, source="prove", description=COUNT,
        target_kind="comment", project_root=str(tmp_path),
    )
    foundry_add_observation(
        cycle=2, source="prove", description=ENUMERATION,
        target_kind="comment", project_root=str(tmp_path),
    )

    everything = foundry_query_observations(project_root=str(tmp_path))
    assert everything["summary"]["total"] == 3
    assert everything["summary"]["by_classification"] == {
        "LINE_DRIFT_CITE": 1,
        "PROSE_COUNT": 1,
        "ENUMERATION": 1,
    }
    assert everything["summary"]["by_source"] == {"trace": 1, "prove": 2}

    by_cycle = foundry_query_observations(cycle=2, project_root=str(tmp_path))
    assert len(by_cycle["observations"]) == 2
    by_class = foundry_query_observations(
        classification="PROSE_COUNT", project_root=str(tmp_path)
    )
    assert len(by_class["observations"]) == 1
    by_source = foundry_query_observations(
        source="trace", project_root=str(tmp_path)
    )
    assert len(by_source["observations"]) == 1


def test_query_observations_surfaces_the_tripwire(
    run: Path, tmp_path: Path
) -> None:
    """A validator checking for demotion attempts must not need to know the
    ledger's file layout."""
    foundry_add_observation(
        cycle=1, source="assay", description=SECURITY,
        target_kind="comment", project_root=str(tmp_path),
    )
    result = foundry_query_observations(project_root=str(tmp_path))
    assert result["summary"]["tripwire_fired"] == 1
    assert result["tripwire"][0]["denylist_class"] == "SECURITY_PROPERTY_CLAIM"


# --- run-directory guard (house rule 3) -------------------------------------
@pytest.mark.parametrize(
    "call",
    [
        lambda root: foundry_add_observation(
            cycle=1, source="trace", description=DRIFT, project_root=root
        ),
        lambda root: foundry_query_observations(project_root=root),
    ],
)
def test_tools_guard_on_no_active_run(tmp_path: Path, call) -> None:
    """Every MCP tool entry point opens with the run-directory guard before
    touching any path."""
    clear_active_run()
    result = call(str(tmp_path))
    assert result["error"] == "No active foundry run. Call Foundry-Init."


def test_query_defects_still_reports_only_defects(run: Path, tmp_path: Path) -> None:
    """No-regression — the pre-existing defect query is unchanged by the split."""
    foundry_add_defect(
        cycle=1, source="trace", defect_type="MISSING",
        description="a real one", project_root=str(tmp_path),
    )
    foundry_add_observation(
        cycle=1, source="trace", description=DRIFT,
        target_kind="comment", project_root=str(tmp_path),
    )
    result = foundry_query_defects(project_root=str(tmp_path))
    assert result["summary"]["total"] == 1
    assert result["summary"]["open"] == 1
    assert result["defects"][0]["description"] == "a real one"
