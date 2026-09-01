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
standard. ``test_undeclared_subject_is_never_refused``,
``test_denylist_outranks_observation_class`` and the D-093 promote-direction
battery below are the tests that fail if a future change lets a real defect
slip into the non-blocking channel — or, in the battery's case, if it lets one
be blocked from the blocking channel, which costs exactly as much.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from foundry_mcp.schemas.vocab import (
    NEVER_DEMOTE_CLASSES,
    OBSERVATION_CLASSES,
    observation_class as vocab_observation_class,
)
from foundry_mcp.tools.foundry import (
    allocate_record_id,
    asserts_code_behaviour,
    foundry_add_defect,
    foundry_add_observation,
    foundry_add_verdict,
    foundry_init,
    foundry_query_defects,
    foundry_query_observations,
    ledger_transaction,
    record_denylist_tripwire,
)
from foundry_mcp.tools.foundry_state import clear_active_run

# Descriptions engineered to match exactly one vocab observation class.
DRIFT = "The cite's line number is stale after the refactor."
COUNT = "The docstring says 8 items but there are 9 now."
DIRECTION = "The comment says the helper is defined above, but it moved."
ENUMERATION = "The comment's list of streams omits flow_trace."

# Matches DIRECTION_WORD as prose, but is a real behavioural defect. The gate
# must never refuse this one on description alone.
#
# D-093: it used to, and this constant's own comment was the accusation — the
# test below asserted the refusal it says must never happen. "returns" is what
# now rescues it, and no denylist entry is involved.
REAL_DEFECT_PROSE = "The handler below returns the wrong status code."

SECURITY = "The comment claims the handler validates the CSRF token; it does not."

# D-093's ten-case battery, verbatim from the drive PROVE re-ran at dc225f8.
# Textbook security-property claims in the phrasings engineers actually write.
# Eight of ten were demotable with the tripwire silent, and two of those were
# additionally REFUSED by ``foundry_add_defect`` — classified DIRECTION_WORD on
# the strength of the word "above" — so the stream could not file them at all.
#
# They are pinned as an INVARIANT ("this files as a defect"), never as a
# mechanism: casting 1 widened vocab's denylist against the same battery in the
# same cycle, and a test that asserted WHICH guard admitted them would pass for
# the wrong reason the moment either half moved.
SECURITY_BATTERY = [
    pytest.param(
        "The comment above validate_token() claims the token signature is "
        "verified, but the function never checks the HMAC.",
        id="hmac-signature",
    ),
    pytest.param(
        "The comment below compare_digest says the comparison is "
        "constant-time, but it uses == so it is not.",
        id="constant-time-compare",
    ),
    pytest.param(
        "The docstring states the endpoint is rate-limited; no rate limiting "
        "exists in the handler.",
        id="rate-limit",
    ),
    pytest.param(
        "The comment claims the payload is validated before use, but no "
        "validation runs — untrusted input reaches the shell.",
        id="input-validation",
    ),
    pytest.param(
        "The comment says the value is hashed before storage; it is stored in "
        "plaintext.",
        id="stored-plaintext",
    ),
    pytest.param(
        "The comment says CORS is locked to the allowlist, but the header is "
        "set to *.",
        id="cors",
    ),
    pytest.param(
        "The comment promises replay protection via a nonce, but the nonce is "
        "never checked.",
        id="nonce-replay",
    ),
    pytest.param(
        "The comment claims the index is bounds-checked, but there is no "
        "bounds check — a buffer overread.",
        id="bounds-check",
    ),
    pytest.param(
        "The comment above the handler claims authentication is enforced, but "
        "no auth check runs.",
        id="auth",
    ),
    pytest.param(
        "The comment says the password is salted, but it is not.",
        id="password-salt",
    ),
]

# The battery's eleventh case, and the only one that proves the repair is
# STRUCTURAL. It carries no security vocabulary at all, so no widening of
# vocab's denylist can ever rescue it, and its prose classifies as
# DIRECTION_WORD ("above" near "but the"). Before D-093's fix it was blocked;
# only the promote-direction fail-safe lets it through.
NO_SECURITY_VOCABULARY = (
    "The comment above the cache helper says the dict is reused, but the "
    "handler rebuilds it on every call and never stores the result."
)


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


def _set_server_cycle(fdir: Path, cycle: int) -> None:
    """Advance the server-owned cycle counter the way the GRIND -> INSPECT
    boundary handler does, without importing the orchestrator."""
    state_path = fdir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["cycle"] = cycle
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _drop_server_cycle(fdir: Path) -> None:
    """Make the run look like a legacy archive: a state.json with no counter."""
    state_path = fdir / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.pop("cycle", None)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


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
    # The hint names the ACTION, and names it as a tool the caller can invoke
    # rather than as a ledger it would have to find. "Foundry-Observation" is
    # the registered MCP tool name; the handler is named too, so the hint is
    # actionable from either side of the boundary.
    assert "Foundry-Observation" in result["hint"]
    assert "foundry_add_observation" in result["hint"]
    # And it forecloses the wrong fix: re-wording prose to slip past the gate.
    assert "Do NOT re-word" in result["hint"]
    # OT-001 — it never reaches defects.json.
    assert _defects(run) == []


def test_refused_finding_is_accepted_as_an_observation(
    run: Path, tmp_path: Path
) -> None:
    """AC-001 — the SAME finding the defect ledger refused is recordable in the
    observations ledger, and lands there with its class."""
    _set_server_cycle(run, 2)
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
    demote." Refusing a defect IS a demotion.

    The fixture is textbook comment prose — DIRECTION_WORD on its face — so the
    ONLY thing keeping it out of the observations channel is the missing
    declaration. It used to be ``REAL_DEFECT_PROSE``, which stopped isolating
    the declaration once D-093 made a substantive finding pass regardless: the
    pair would have gone green for a second reason and stopped guarding this
    one."""
    result = foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="BROKEN",
        description=DIRECTION,
        project_root=str(tmp_path),
    )
    assert "error" not in result, result
    assert result["defect_id"] == "D-001"
    assert _defects(run)[0]["description"] == DIRECTION


def test_same_prose_is_refused_once_declared_a_comment(
    run: Path, tmp_path: Path
) -> None:
    """The mirror of the test above: the identical description IS refused once
    the caller declares the subject is a comment. Declaration is the whole
    discriminator — the two calls differ in exactly one argument."""
    result = foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="BROKEN",
        description=DIRECTION,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert result["refused_class"] == "DIRECTION_WORD", result
    assert _defects(run) == []


# --- D-093 — the promote-direction fail-safe --------------------------------
def test_a_substantive_finding_is_never_refused_however_its_prose_reads(
    run: Path, tmp_path: Path
) -> None:
    """D-093 root cause 2, in one line of prose.

    ``REAL_DEFECT_PROSE`` is a real behavioural defect whose wording trips the
    loose DIRECTION_WORD regex ("below" near "wrong"). The constant has carried
    the comment "the gate must never refuse this one on description alone"
    since the split shipped — while the test beside it asserted the gate DID
    refuse it, on nothing but that description, the moment a caller declared
    the subject a comment.

    That inverted the effort's binding constraint: before the split
    ``foundry_add_defect`` validated nothing and this filing succeeded, so the
    verification loop's defect standard was measurably WEAKER at HEAD than
    before the change meant to strengthen it. A declaration must be able to
    route a finding to the right ledger; it must never be able to delete one.
    """
    result = foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="BROKEN",
        description=REAL_DEFECT_PROSE,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert "error" not in result, result
    assert _defects(run)[0]["description"] == REAL_DEFECT_PROSE


@pytest.mark.parametrize("description", SECURITY_BATTERY)
def test_security_property_claims_always_file_as_defects(
    run: Path, tmp_path: Path, description: str
) -> None:
    """OT-002, driven over the phrasings a stream actually writes.

    "A comment claiming a security property the code does not implement is
    filed as a DEFECT and cannot be demoted to observation." The suite went
    green on this for eleven cycles while eight of these ten failed, because
    every security assertion in this file reused ONE hand-tuned string
    containing the literal "CSRF" — a token the denylist regex already knew.
    Pinning a guard only where it already works is how a guard stays broken.

    Each case declares ``target_kind="comment"``, which is the honest
    declaration: the finding IS about a comment, one that lies. That
    declaration is exactly what used to make the filing refusable.
    """
    result = foundry_add_defect(
        cycle=1,
        source="prove",
        defect_type="WRONG",
        description=description,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert "error" not in result, result
    assert _defects(run)[0]["description"] == description


@pytest.mark.parametrize("description", SECURITY_BATTERY)
def test_the_fail_safe_alone_rescues_every_battery_case(description: str) -> None:
    """…and it rescues them WITHOUT the denylist's help.

    The test above would pass if either guard admitted the finding. This one
    asserts the property that makes the repair structural: the promote-side
    fail-safe recognises every case on its own, so a denylist that has never
    heard of the next security noun cannot cost a defect. It is deliberately a
    unit assertion on this module's own predicate — asserting anything about
    ``never_demote_class`` here would pin a vocabulary another casting owns.
    """
    assert asserts_code_behaviour({"description": description}) is True


def test_a_substantive_finding_with_no_security_vocabulary_still_files(
    run: Path, tmp_path: Path
) -> None:
    """The case no widening of the denylist could ever have reached.

    Not a security finding at all — a plain performance/correctness one — with
    prose that classifies as DIRECTION_WORD. D-093's first root cause was that
    vocab's denylist did not know words like "signature" and "plaintext", and
    casting 1 widened it. This finding contains no such word to widen toward,
    and it was blocked by the same mechanism. Only the second root cause's fix
    lets it through, which is why this test is the one that fails if the
    promote-direction guard is ever removed as redundant.
    """
    finding = {"description": NO_SECURITY_VOCABULARY, "target_kind": "comment"}
    assert vocab_observation_class(finding) == "DIRECTION_WORD", (
        "the fixture no longer trips an observation regex, so it no longer "
        "exercises the promote-direction guard at all"
    )

    result = foundry_add_defect(
        cycle=1,
        source="assay",
        defect_type="WRONG",
        description=NO_SECURITY_VOCABULARY,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert "error" not in result, result
    assert _defects(run)[0]["description"] == NO_SECURITY_VOCABULARY


@pytest.mark.parametrize(
    "description", [DRIFT, COUNT, DIRECTION, ENUMERATION]
)
def test_the_fail_safe_is_silent_on_real_comment_prose(description: str) -> None:
    """The boundary that keeps AC-001 from being gutted by its own fix.

    The promote-side guard is biased to over-match, and over-matching is the
    safe direction — but a guard that matched EVERYTHING would refuse nothing
    and quietly delete the observation channel. These four are the canonical
    comment-prose findings AC-001 names; the guard must stay silent on all of
    them, or the refusal above it can never fire again."""
    assert asserts_code_behaviour({"description": description}) is False


@pytest.mark.parametrize(
    "finding", [{}, {"description": None}, {"description": 17}, {"description": []}]
)
def test_the_fail_safe_is_total_over_a_malformed_finding(finding: dict) -> None:
    """Never raises, matching vocab's predicate contract: a missing or
    wrong-typed description means "no match", not an exception across the MCP
    boundary."""
    assert asserts_code_behaviour(finding) is False


def test_the_fail_safe_did_not_open_the_demotion_channel(
    run: Path, tmp_path: Path
) -> None:
    """The no-regression half. Making the PROMOTE direction fail safe must not
    relax the DEMOTE direction: a denylisted finding is still rejected as an
    observation and still fires the audit tripwire, and ordinary comment prose
    still records cleanly."""
    denied = foundry_add_observation(
        cycle=1,
        source="prove",
        description=SECURITY,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert denied["denylist_class"] == "SECURITY_PROPERTY_CLAIM", denied
    assert len(_observations(run)["tripwire"]) == 1

    recorded = foundry_add_observation(
        cycle=1,
        source="prove",
        description=DRIFT,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert recorded.get("observation_id"), recorded
    assert len(_observations(run)["observations"]) == 1


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


def test_omitting_target_kind_entirely_is_refused_and_audited(
    run: Path, tmp_path: Path
) -> None:
    """D-069 / AC-002 — the demotion path fails CLOSED when the argument is not
    passed AT ALL, not merely when it is passed empty.

    The matched pair PROVE drove: the same finding, the same classification,
    one argument apart. It is deliberately worded so no OTHER denylist entry
    can match — no security vocabulary, no "spec requires", no cite — so the
    only thing standing between it and the observations ledger is the
    NON_COMMENT branch.

    The writer used to carry ``target_kind: str = "comment"``, so omission —
    the DEFAULT behaviour of every caller, since the field is optional in the
    advertised schema — fabricated the declaration the denylist checks and a
    real code-behaviour finding was demoted out of the blocking ledger with the
    tripwire silent. Recording an observation IS the demotion, so absence must
    reach the same refusal a declared non-comment gets.
    """
    perf = "The handler rebuilds the lookup dict on every call where one cached copy would do."

    declared = foundry_add_observation(
        cycle=1,
        source="prove",
        description=perf,
        classification="PROSE_COUNT",
        target_kind="function",
        project_root=str(tmp_path),
    )
    omitted = foundry_add_observation(
        cycle=1,
        source="prove",
        description=perf,
        classification="PROSE_COUNT",
        # target_kind deliberately NOT passed — the bypass, verbatim.
        project_root=str(tmp_path),
    )

    # Both halves of the pair reach the same verdict.
    assert declared["denylist_class"] == "NON_COMMENT", declared
    assert omitted["denylist_class"] == "NON_COMMENT", omitted
    # …and the refusal names the missing field and the action, so the caller
    # is not left guessing that re-wording is the repair.
    assert omitted["missing_field"] == "target_kind", omitted
    assert "target_kind" in omitted["hint"] and "Foundry-Defect" in omitted["hint"]

    ledger = _observations(run)
    # Nothing was demoted…
    assert ledger["observations"] == []
    # …and the audit signal fired for BOTH attempts, durably. It used to be
    # silent on exactly the one that got through.
    assert len(ledger["tripwire"]) == 2, ledger["tripwire"]
    assert [t["denylist_class"] for t in ledger["tripwire"]] == [
        "NON_COMMENT",
        "NON_COMMENT",
    ]
    assert "no target_kind was declared" in ledger["tripwire"][1]["detail"]


def test_declared_comment_still_records_after_the_fail_closed_default(
    run: Path, tmp_path: Path
) -> None:
    """D-069's other half: failing closed on absence must not close the channel
    itself. A stream that DECLARES its subject a comment still gets its finding
    into the observations ledger."""
    result = foundry_add_observation(
        cycle=1,
        source="prove",
        description=DRIFT,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert result.get("observation_id"), result
    assert _observations(run)["tripwire"] == []
    assert len(_observations(run)["observations"]) == 1


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


def test_both_placement_spellings_persist_as_one_canonical_type(
    run: Path, tmp_path: Path
) -> None:
    """MISPLACED and ARCHITECTURAL_PLACEMENT are ONE type under two live agent
    spellings. Both are ACCEPTED on input — neither contract may break — and
    both PERSIST as the canonical spelling.

    Storing the raw value made the same finding two different types depending
    on which door it came through: `foundry_sync_defects` already folds via
    `vocab.canonical_defect_type`, so a MISPLACED filed through Foundry-Defect
    could never cluster with one filed through Foundry-Sync, and the escalation
    counter that keys on type saw two half-populated classes instead of one.

    This is normalisation, not the coercion CT-002 forbids: that rule governs
    UNKNOWN values, which the membership check rejects by name."""
    for i, spelling in enumerate(("MISPLACED", "ARCHITECTURAL_PLACEMENT")):
        result = foundry_add_defect(
            cycle=1,
            source="trace",
            defect_type=spelling,
            description=f"placement finding {i}",
            project_root=str(tmp_path),
        )
        assert "error" not in result, result
        # The caller is told what was actually stored, not what it sent.
        assert result["type"] == "ARCHITECTURAL_PLACEMENT"

    assert [d["type"] for d in _defects(run)] == [
        "ARCHITECTURAL_PLACEMENT",
        "ARCHITECTURAL_PLACEMENT",
    ]
    # The forge-log mirror agrees with the ledger — a reader must not see one
    # spelling in the JSON and another in the markdown.
    log = (run / "forge-log.md").read_text(encoding="utf-8")
    assert "MISPLACED" not in log


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

    # Every record above was stamped with the SERVER's cycle (0 on a fresh
    # run), whatever the caller asserted — so the cycle filter selects all
    # three, not the two whose argument said 2. That is the point of ST-001:
    # the filter and the stamp read the same counter.
    by_cycle = foundry_query_observations(cycle=0, project_root=str(tmp_path))
    assert len(by_cycle["observations"]) == 3
    assert foundry_query_observations(
        cycle=2, project_root=str(tmp_path)
    )["observations"] == []
    by_class = foundry_query_observations(
        classification="PROSE_COUNT", project_root=str(tmp_path)
    )
    assert len(by_class["observations"]) == 1
    by_source = foundry_query_observations(
        source="trace", project_root=str(tmp_path)
    )
    assert len(by_source["observations"]) == 1


# --- ST-001 — the server owns the cycle number ------------------------------
def test_defect_is_stamped_with_the_server_cycle_not_the_callers(
    run: Path, tmp_path: Path
) -> None:
    """ST-001 — where a server-side counter exists it is the authority, and a
    caller-supplied cycle is not trusted against it.

    grand-vulture's state.json read `"cycle": 0` for its entire life while its
    defects carried lead-asserted cycles 0-17, because every cycle number in
    the data model was an argument rather than a fact. Escalation counts
    consecutive cycles per class and the roll-up is keyed by cycle: both are
    meaningless against a number the caller picked."""
    _set_server_cycle(run, 7)
    result = foundry_add_defect(
        cycle=99,  # the lead's assertion — wrong, and ignored
        source="trace",
        defect_type="WRONG",
        description="Handler returns 200 on a validation failure.",
        project_root=str(tmp_path),
    )
    assert result["cycle"] == 7, result
    assert _defects(run)[0]["cycle"] == 7
    # The human-readable mirror must not disagree with the ledger.
    assert "Cycle 7" in (run / "forge-log.md").read_text(encoding="utf-8")


def test_observation_is_stamped_with_the_server_cycle(
    run: Path, tmp_path: Path
) -> None:
    """Both ledgers read the same counter, or the per-cycle roll-up cannot join
    an observation to the defects filed beside it."""
    _set_server_cycle(run, 4)
    result = foundry_add_observation(
        cycle=99,
        source="prove",
        description=DRIFT,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    assert result["cycle"] == 4, result
    assert _observations(run)["observations"][0]["cycle"] == 4


def test_verdict_is_stamped_with_the_server_cycle(
    run: Path, tmp_path: Path
) -> None:
    """The third cycle-stamped record type. A verdict recorded under a
    lead-asserted number cannot be joined against the cycle's defects."""
    _set_server_cycle(run, 5)
    result = foundry_add_verdict(
        requirement_id="AC-025",
        verdict="VERIFIED",
        evidence="tests/test_observations.py",
        cycle=99,
        project_root=str(tmp_path),
    )
    assert result["cycle"] == 5, result
    verdicts = json.loads((run / "verdicts.json").read_text(encoding="utf-8"))
    assert verdicts["cycle"] == 5
    assert verdicts["requirements"][0]["cycle"] == 5


def test_an_absent_counter_stamps_zero_and_keeps_the_callers_claim(
    run: Path, tmp_path: Path
) -> None:
    """D-119 — this test asserted the OPPOSITE until the lead's interface
    ruling. A run whose state.json predates the counter used to keep the
    caller's number, on the reasoning that the server had "no better answer".
    It does: 0. Trusting the caller in the degraded case is precisely what
    ST-001 exists to remove, ``foundry_orchestrator._current_cycle`` has
    resolved this input to 0 since D-059, and a filing door that disagrees with
    its sibling about WHICH cycle a record belongs to breaks escalation's
    consecutive-cycle count no matter which door is "right".

    The caller is not silently overruled — its 12 is on the record.
    """
    _drop_server_cycle(run)
    result = foundry_add_defect(
        cycle=12,
        source="trace",
        defect_type="WRONG",
        description="Legacy archive finding.",
        project_root=str(tmp_path),
    )
    assert result["cycle"] == 0, result
    assert result["declared_cycle"] == 12, result
    assert _defects(run)[0]["cycle"] == 0
    assert _defects(run)[0]["declared_cycle"] == 12


# The defect report's matrix verbatim, and the same six cases
# ``test_escalation._MALFORMED_COUNTERS`` drives through BOTH doors: a counter
# that is absent, null, a string, negative, a float, or a bool is not a counter.
# `True` earns its own case because bool is an int subclass and would otherwise
# stamp cycle 1.
_MALFORMED_COUNTERS = [
    pytest.param("no-key", None, id="no-key"),
    pytest.param("value", None, id="null"),
    pytest.param("value", "3", id="str"),
    pytest.param("value", -3, id="negative"),
    pytest.param("value", 2.5, id="float"),
    pytest.param("value", True, id="bool"),
]


@pytest.mark.parametrize("mode,bogus", _MALFORMED_COUNTERS)
def test_a_malformed_counter_stamps_zero_not_the_callers_cycle(
    run: Path, tmp_path: Path, mode: str, bogus: object
) -> None:
    """D-119, the single door's half of the cross-door contract.

    ``foundry.py`` used to read a malformed counter as "no counter" and stamp
    the caller's 8, while ``foundry_orchestrator.py`` read the identical file
    and stamped 0. Identical findings filed through Foundry-Defect and
    Foundry-Sync therefore landed in different cycles: mixed filing persisted
    [1,0,3] where one door alone would have persisted [1,2,3], the longest
    consecutive run was 2, and a genuine systemic class evaded ST-002
    escalation while the AC-011 DONE guard passed.

    ``test_escalation.test_both_filing_doors_stamp_the_same_cycle`` pins the
    two doors against each other over this same matrix; this pins THIS door
    against the ruling on its own, so the contract is verifiable here without
    reading the sibling's tests.
    """
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if mode == "no-key":
        state.pop("cycle", None)
    else:
        state["cycle"] = bogus
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    result = foundry_add_defect(
        cycle=8,
        source="trace",
        defect_type="WRONG",
        description="Finding filed against a corrupt state file.",
        project_root=str(tmp_path),
    )
    assert result["cycle"] == 0, result
    assert _defects(run)[0]["cycle"] == 0
    # The claim survives beside the stamp — the ruling's auditability half.
    assert result["declared_cycle"] == 8, result
    assert _defects(run)[0]["declared_cycle"] == 8
    # The human-readable mirror must not disagree with the ledger.
    assert "Cycle 0" in (run / "forge-log.md").read_text(encoding="utf-8")


def test_a_healthy_counter_still_outranks_the_caller_and_keeps_the_claim(
    run: Path, tmp_path: Path
) -> None:
    """NFR-002 guard on the ruling: D-119 changes the MALFORMED path only. A
    real counter is still the authority and the caller's number is still
    ignored — it is now merely recorded as well."""
    _set_server_cycle(run, 6)
    result = foundry_add_defect(
        cycle=99,
        source="trace",
        defect_type="WRONG",
        description="Filed against a healthy counter.",
        project_root=str(tmp_path),
    )
    assert result["cycle"] == 6, result
    assert result["declared_cycle"] == 99, result
    assert _defects(run)[0]["cycle"] == 6
    assert _defects(run)[0]["declared_cycle"] == 99


# --- AC-002 — the tripwire is reachable, not just present -------------------
def test_tripwire_fires_through_the_public_observation_surface(
    run: Path, tmp_path: Path
) -> None:
    """AC-002 — a denylisted finding arriving through the Foundry-Observation
    surface is rejected AND drives the tripwire non-empty.

    The tripwire existed but no MCP path could reach it: every production
    caller pre-filtered on the denylist before calling the writer, so the
    writer's inner check could not fire and `observations.json.tripwire` stayed
    `[]` across every denylist scenario. This drives it through the handler the
    server registers as `Foundry-Observation`."""
    ledger = _observations(run)
    assert ledger["tripwire"] == [], "precondition: tripwire starts empty"

    result = foundry_add_observation(
        cycle=1,
        source="prove",
        description=SECURITY,
        target_kind="comment",
        project_root=str(tmp_path),
    )

    assert "error" in result, result
    assert result["denylist_class"] == "SECURITY_PROPERTY_CLAIM"
    fired = _observations(run)["tripwire"]
    assert len(fired) == 1, "the tripwire must be reachable from a public path"
    assert fired[0]["denylist_class"] == "SECURITY_PROPERTY_CLAIM"
    assert fired[0]["source"] == "prove"
    assert fired[0]["description"] == SECURITY
    assert fired[0]["fired_at"]
    # Durable in BOTH channels — a lead reads the log, a validator reads JSON.
    assert "TRIPWIRE" in (run / "forge-log.md").read_text(encoding="utf-8")
    # Fail-safe: the finding did NOT become an observation.
    assert _observations(run)["observations"] == []


def test_tripwire_recorder_is_callable_by_a_prefiltering_caller(
    run: Path, tmp_path: Path
) -> None:
    """The decision and the audit signal are ONE exported call, so a caller
    that evaluates the denylist itself still fires the tripwire.

    This is the shape `foundry_sync_defects`'s auto-demotion branch needs: it
    computes `never_demote_class` before deciding whether to route a finding
    out of the defect ledger, and that pre-filter is exactly what made the
    signal unreachable while it lived inside the writer's body."""
    finding = {
        "description": SECURITY,
        "spec_ref": "",
        "target_kind": "comment",
        "symbol": "validate_request",
        "file": "src/api/auth.py",
    }
    record = record_denylist_tripwire(run, finding, cycle=3, source="assay")

    assert record is not None, "a denylisted finding must fire the tripwire"
    assert record["denylist_class"] == "SECURITY_PROPERTY_CLAIM"
    assert record["symbol"] == "validate_request"
    assert record["file"] == "src/api/auth.py"
    assert _observations(run)["tripwire"] == [record]

    # And it stays silent on a legitimate demotion, so the signal keeps meaning
    # something: a tripwire that fired on correct behaviour would be noise.
    assert record_denylist_tripwire(
        run,
        {"description": DRIFT, "spec_ref": "", "target_kind": "comment"},
        cycle=3,
        source="assay",
    ) is None
    assert len(_observations(run)["tripwire"]) == 1


def test_undeclared_subject_fires_the_tripwire_as_non_comment(
    run: Path, tmp_path: Path
) -> None:
    """"Anything non-comment" can never be an observation, and an UNDECLARED
    subject cannot be shown to be a comment. Absence is not a licence to
    demote in either direction — here it is refused and audited."""
    result = foundry_add_observation(
        cycle=1,
        source="trace",
        description=DRIFT,
        target_kind="",  # nothing declared
        project_root=str(tmp_path),
    )
    assert result["denylist_class"] == "NON_COMMENT"
    assert _observations(run)["tripwire"][0]["denylist_class"] == "NON_COMMENT"
    assert _observations(run)["observations"] == []


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


# --------------------------------------------------------------------------- #
# D-095 / D-096 / D-097 / D-099 — the persistence layer under a malformed
# ledger. One packet: the layer must not RAISE, must not LOSE data, and must
# NAME the file at fault. Each property is separately load-bearing, and the
# ones that matter most are the two negatives — a corrupt ledger is never
# written over, and a good filing is never discarded.
# --------------------------------------------------------------------------- #

#: Every way a run artifact arrives broken in practice: a git merge conflict or
#: a disk-full write truncates it, an editor writes a bare literal, a migration
#: writes the wrong container type. Parameterised rather than spelled out per
#: case so a new corruption shape is one line, not one test.
_CORRUPT_DOCUMENTS = [
    pytest.param('{"defects": [{"id": "D-00', id="truncated"),
    pytest.param("[]", id="top-level-list"),
    pytest.param("null", id="null"),
    pytest.param("42", id="number"),
    pytest.param('"a string"', id="string"),
    pytest.param("<<<<<<< HEAD\n{}\n=======\n{}\n>>>>>>> other\n", id="merge-conflict"),
]


def _write_defects(fdir: Path, raw: str) -> None:
    (fdir / "defects.json").write_text(raw, encoding="utf-8")


@pytest.mark.parametrize("raw", _CORRUPT_DOCUMENTS)
def test_filing_a_defect_against_a_corrupt_ledger_is_a_named_refusal(
    run: Path, tmp_path: Path, raw: str
) -> None:
    """D-095. The tool refuses, NAMES the file, and does not raise.

    22 of 24 corruption x entry-point combinations used to raise straight
    across the MCP boundary, and not one named the offending artifact — so the
    operator was handed a traceback and no way to tell which of six JSON files
    to repair.
    """
    _write_defects(run, raw)
    result = foundry_add_defect(
        cycle=1, source="trace", defect_type="WRONG",
        description="a real behavioural defect", project_root=str(tmp_path),
    )
    assert "defects.json" in result["error"], result
    assert result["corrupt_artifacts"], result
    assert "defect_id" not in result


@pytest.mark.parametrize("raw", _CORRUPT_DOCUMENTS)
def test_querying_a_corrupt_ledger_is_a_named_refusal(
    run: Path, tmp_path: Path, raw: str
) -> None:
    """D-095, the half that made the failure undiagnosable. The QUERY path was
    holed identically, so the one tool an operator would reach for to inspect a
    broken ledger raised on it too."""
    _write_defects(run, raw)
    result = foundry_query_defects(project_root=str(tmp_path))
    assert "defects.json" in result["error"], result


def test_a_corrupt_ledger_is_never_written_over(run: Path, tmp_path: Path) -> None:
    """D-095 + D-096 together: refusing is only half the property. The bytes
    that were on disk must still be on disk afterwards, because a ledger that
    is merely unreadable can be repaired and one that has been overwritten
    cannot."""
    raw = '{"defects": [{"id": "D-00'
    _write_defects(run, raw)
    foundry_add_defect(
        cycle=1, source="trace", defect_type="WRONG",
        description="a real behavioural defect", project_root=str(tmp_path),
    )
    assert (run / "defects.json").read_text(encoding="utf-8") == raw


def test_object_valued_defects_container_refuses_without_destroying(
    run: Path, tmp_path: Path
) -> None:
    """D-096, the worst of the group. Seeding ``defects.json`` with an
    OBJECT-valued ``defects`` key made the filing path discard every record the
    file held, re-mint ``D-001`` and return ``{"defect_id": "D-001",
    "total_defects": 1}`` — success, over the top of the run's whole ledger. A
    sibling key survived the write, which is what proved the write had
    completed and only the records had been thrown away.

    That is precisely the "the race lost a DEFECT, not just an id" failure the
    transaction's own header comment says it exists to prevent, reached through
    the container instead of through the race.
    """
    prior = {
        "defects": {"D-001": {"id": "D-001", "status": "open"}},
        "sibling": "must survive",
    }
    _write_defects(run, json.dumps(prior))

    result = foundry_add_defect(
        cycle=1, source="trace", defect_type="WRONG",
        description="a real behavioural defect", project_root=str(tmp_path),
    )

    assert "defect_id" not in result, "the filing must NOT report success"
    assert "defects.json" in result["error"]
    assert "not a list" in result["error"]
    assert json.loads((run / "defects.json").read_text(encoding="utf-8")) == prior


def test_a_malformed_record_does_not_discard_the_new_filing(
    run: Path, tmp_path: Path
) -> None:
    """D-097. A non-dict record already in the list made the open-count scan
    raise — and it raised AFTER ``defects.append()``, so the transaction
    aborted, the newly filed defect was silently discarded, and the caller got
    an exception rather than a refusal.

    ``allocate_record_id`` four lines above already skipped such a record. The
    fix is that the rest of the scan agrees with it, so one malformed
    historical record cannot cost a good new filing.
    """
    _write_defects(run, json.dumps({
        "defects": ["i am not a record", {"id": "D-002", "status": "open"}],
    }))

    result = foundry_add_defect(
        cycle=1, source="trace", defect_type="PARTIAL",
        description="a real behavioural defect", project_root=str(tmp_path),
    )

    assert result["defect_id"] == "D-003", result
    stored = json.loads((run / "defects.json").read_text(encoding="utf-8"))["defects"]
    ids = [d["id"] for d in stored if isinstance(d, dict)]
    assert ids == ["D-002", "D-003"], "the new filing must survive"
    assert "i am not a record" in stored, "and the junk record is left alone"


def test_a_malformed_record_does_not_brick_the_queries(
    run: Path, tmp_path: Path
) -> None:
    """D-097 on the read side — the same skip, so a ledger carrying one bad
    record can still be inspected."""
    _write_defects(run, json.dumps({
        "defects": [None, {"id": "D-001", "status": "open", "source": "trace"}],
    }))
    result = foundry_query_defects(project_root=str(tmp_path))
    assert result["summary"]["total"] == 1
    assert result["summary"]["open"] == 1


def test_ledger_transaction_refuses_a_non_list_container(run: Path) -> None:
    """D-096 at the primitive. The backstop for a caller that skips the guard:
    it raises and writes NOTHING, rather than coercing the container to ``[]``
    and reporting success. ``foundry_orchestrator``'s two ledger writers import
    this primitive, so the refusal has to live here and not only at this
    module's entry points."""
    from foundry_mcp.tools.foundry import LedgerShapeError

    path = run / "defects.json"
    prior = {"defects": {"not": "a list"}}
    path.write_text(json.dumps(prior), encoding="utf-8")

    with pytest.raises(LedgerShapeError) as exc:
        with ledger_transaction(path, "defects") as records:
            records.append({"id": "D-001"})

    assert "defects.json" in str(exc.value)
    assert json.loads(path.read_text(encoding="utf-8")) == prior


def test_ledger_transaction_nests_on_the_same_thread(run: Path) -> None:
    """D-099. The docstring promised same-thread nesting was safe and it hung
    forever: an ``fcntl`` lock is held per OPEN FILE DESCRIPTION, and the
    transaction opens a fresh fd on each entry, so a nested acquire blocked on
    a lock the same thread already held. The ``RLock`` covered threads and
    hid nothing of this.

    Driven on ONE path under TWO collection keys, which is the shape that
    actually occurs — ``observations.json`` carries records under both
    ``observations`` and ``tripwire`` — and the shape a naive path-keyed cache
    would get wrong by yielding one list for both.

    Run on a worker thread with a join timeout so that a regression FAILS the
    suite instead of hanging it.
    """
    path = run / "observations.json"
    finished: list[str] = []

    def _nested() -> None:
        with ledger_transaction(path, "observations") as observations:
            observations.append({"id": "O-001"})
            with ledger_transaction(path, "tripwire") as tripwire:
                tripwire.append({"denylist_class": "SECURITY_PROPERTY"})
        finished.append("ok")

    worker = threading.Thread(target=_nested, daemon=True)
    worker.start()
    worker.join(timeout=10)

    assert finished == ["ok"], "nested ledger_transaction deadlocked"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert [o["id"] for o in stored["observations"]] == ["O-001"]
    assert len(stored["tripwire"]) == 1, "the inner write must survive the outer"


def test_nested_transaction_defers_to_one_write(run: Path) -> None:
    """D-099's other half: nesting must produce ONE document and one write, not
    two racing ones. An inner block that mutated a stale copy would lose the
    outer block's append on the outer's exit."""
    path = run / "observations.json"
    with ledger_transaction(path, "observations") as outer:
        outer.append({"id": "O-001"})
        with ledger_transaction(path, "observations") as inner:
            assert inner is outer, "nesting must yield the SAME list"
            inner.append({"id": "O-002"})

    stored = json.loads(path.read_text(encoding="utf-8"))["observations"]
    assert [o["id"] for o in stored] == ["O-001", "O-002"]


def test_resume_names_a_corrupt_state_file(tmp_path: Path) -> None:
    """D-095 at the moment it costs most. Resuming is exactly what an operator
    does after the crash that corrupted the run, and the resume path used to
    raise on the file it was trying to help them recover."""
    result = foundry_init(project_root=str(tmp_path))
    fdir = Path(result["foundry_dir"])
    run_name = result["run_name"]
    (fdir / "state.json").write_text("{ truncated", encoding="utf-8")

    resumed = foundry_init(resume=run_name, project_root=str(tmp_path))
    assert "state.json" in resumed["error"], resumed
    assert "resumed" not in resumed
