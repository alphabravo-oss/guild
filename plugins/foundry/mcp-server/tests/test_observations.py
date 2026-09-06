"""process-fixes casting 3 — the observation/defect split, the never-demote
tripwire, the reconciled Foundry-Defect vocabulary, and defect-id uniqueness
under concurrency. The casting number is qualified for the same reason every
requirement id below is: under the convergence spec this file belongs to
casting 2, by the lead ruling recorded in that run's concerns.md.

WHICH SPEC EACH ID BELONGS TO — READ THIS BEFORE GREPPING AN ID HERE
-------------------------------------------------------------------
This module was built under ``forge-specs/foundry-run-process-fixes/spec.md``
(the thunder-viper run) and is maintained under
``forge-specs/foundry-run-convergence/spec.md``. Both specs number their rows
from 1, so an id here means two different things depending on which spec the
reader is holding. That is not a partial overlap to be waved at: EVERY id this
file names — AC-001, AC-002, AC-004, AC-005, AC-006, AC-007, AC-010, AC-011,
AC-019, AC-025, CT-001, CT-002, FR-004, FR-007, FR-020, FR-023, NFR-002,
OT-001, OT-002, OT-008, ST-001, ST-002 — was checked against both files, and
every one of them resolves in BOTH. Not a single id disambiguates itself, so
the qualification below is the only thing that tells them apart. ``AC-001`` is
the comment-prose refusal below in the first spec and the clean-cycles
escalation exit in the second; ``ST-001`` is the server-owned cycle counter
here and the ESCALATED->CLEARED transition there; ``CT-002`` is the reconciled
vocabulary here and the required ``class`` there.

D-178 is what leaving that implicit cost. This docstring tagged the
comment-prose refusal "AC-001 / OT-001" with no spec named, TRACE resolved
those ids against the convergence spec — where they name the escalation exit
driven in ``tests/test_escalation.py`` — and filed the mismatch. The ids were
never wrong; they were UNQUALIFIED. So the convention is now explicit and
holds for every id in this file:

  * ``process-fixes AC-001`` cites
    ``forge-specs/foundry-run-process-fixes/spec.md``;
  * a BARE id cites ``forge-specs/foundry-run-convergence/spec.md``, the spec
    this tree is under;
  * a requirement-shaped string in a KEYWORD ARGUMENT (``spec_ref="AC-007"``,
    ``requirement_id="AC-025"``) is neither. It is fixture input handed to the
    door under test — arbitrary, because ``is_spec_required_behaviour_claim``
    accepts any non-empty ``spec_ref`` — and it cites nothing, exactly as
    ``FIXTURE_CLASS`` below cites nothing.

One regression test per acceptance criterion:

  process-fixes AC-001 / OT-001
      comment-prose filed as a defect is REFUSED naming the class and the
      legal set, the same finding is ACCEPTED into observations.json, and
      defects.json never contains it.
  process-fixes AC-002 / OT-002
      a denylisted finding filed as an observation is REJECTED and the audit
      tripwire fires durably (observations.json + forge-log.md); a
      security-property claim about a comment is a DEFECT and files
      successfully.
  process-fixes AC-004
      a fresh run carries the seeded F0 ruling (see also test_foundry_init.py,
      which asserts the parse).
  process-fixes AC-019 / OT-008
      defect_type PARTIAL is accepted and stored verbatim, with source
      preserved verbatim.
  process-fixes AC-025
      concurrently filed defects get unique ids and BOTH survive.
  process-fixes CT-002
      unknown source / defect_type are rejected server-side with a named
      error, and source is never coerced onto "trace".
  CT-002 / FR-007 / AC-010
      the root-cause `class` is REQUIRED, refused by name when absent. This
      row is convergence numbering, and it is where the two CT-002s meet:
      process-fixes CT-002 governs the reconciled vocabulary, convergence
      CT-002 governs the required class. One test carries both, and says so.
  process-fixes FR-023
      the ledger is typed, per-run, and never mixed into defects.json.
  fallout FR-008 / GI-024 (D-061)
      the package defines ``_artifact_guard`` exactly ONCE. This module's
      scoped guard — the one the refusal test above drives — was a name-alike
      of the leaf's for three cycles and is now ``_named_artifact_guard``, so
      no accounting row has to excuse the collision to a name-keyed sweep.

Every filing below goes through ``_file_defect``, which supplies the ``tier``
(CT-001 / FR-004) and ``class`` (CT-002 / FR-007) both doors now require. Those
two fields are stated once there rather than at twenty-five call sites whose
subject they are not; the refusals themselves are driven in
``tests/test_defect_tier.py``, where they ARE the subject. One filing reaches
past the wrapper on purpose — the class-required half of
``test_defect_class_is_required_and_persisted_under_the_class_key``, which must
arrive without the very field the wrapper exists to supply.

The safety property this file guards hardest is the one that is NOT stated as
an AC but is the whole point of process-fixes A-004: the split must never
weaken the defect standard. ``test_undeclared_subject_is_never_refused``,
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
    foundry_drive_temper_candidate,
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


#: The root-cause class every filing in this module supplies when its own
#: subject is not the class field. Named as a fixture value rather than
#: borrowed from a real taxonomy, because a reader must not mistake it for a
#: finding this file is making about the code under test.
FIXTURE_CLASS = "FIXTURE_ROOT_CAUSE"


def _file_defect(**kwargs) -> dict:
    """``foundry_add_defect`` with the two fields convergence CT-001 and
    convergence CT-002 now require.

    WHY THIS WRAPPER EXISTS
    -----------------------
    ``tier`` (convergence CT-001 / FR-004 / AC-006) and ``class`` (convergence
    CT-002 / FR-007 / AC-010) became REQUIRED at both filing doors, so the
    twenty-five filings in
    this module — every one of which predates the evidence axis — would
    otherwise be refused before reaching the behaviour each test is actually
    about. Not one of those tests is ABOUT the tier: their subjects are the
    observation/defect split, the never-demote tripwire, the reconciled
    vocabulary, cycle stamping, and the persistence layer under a malformed
    ledger. The two fields are therefore stated ONCE, here, rather than
    twenty-five times at sites whose subject they are not.

    ``LIVE`` is the honest default for all of them: each drives the door and
    asserts what came back, which is the reproduction LIVE means. A test whose
    subject IS a LATENT filing passes ``tier="LATENT"`` and its own
    ``reproduction_attempted``; a test whose subject IS the class field passes
    its own ``defect_class``. ``setdefault`` is what makes both overrides win.

    NOTHING IS WEAKENED HERE. This calls the real door with the real
    arguments — no bypass, no relaxed validator, no default supplied inside
    ``foundry.py``. The refusals themselves are driven in
    ``tests/test_defect_tier.py``, where they are the subject.
    """
    kwargs.setdefault("tier", "LIVE")
    kwargs.setdefault("defect_class", FIXTURE_CLASS)
    return foundry_add_defect(**kwargs)


# --- process-fixes AC-001 / OT-001 ------------------------------------------
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
    """process-fixes AC-001 — each of the four comment-prose classes is refused
    by Foundry-Defect, with an error naming the offending class AND the legal
    set."""
    result = _file_defect(
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
    # process-fixes OT-001 — it never reaches defects.json.
    assert _defects(run) == []


def test_refused_finding_is_accepted_as_an_observation(
    run: Path, tmp_path: Path
) -> None:
    """process-fixes AC-001 — the SAME finding the defect ledger refused is
    recordable in the observations ledger, and lands there with its class."""
    _set_server_cycle(run, 2)
    refusal = _file_defect(
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
    # process-fixes FR-023 / OT-001 — never mixed into defects.json.
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
    result = _file_defect(
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
    result = _file_defect(
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
    result = _file_defect(
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
    """process-fixes OT-002, driven over the phrasings a stream actually
    writes.

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
    result = _file_defect(
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

    result = _file_defect(
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
    """The boundary that keeps process-fixes AC-001 from being gutted by its
    own fix.

    The promote-side guard is biased to over-match, and over-matching is the
    safe direction — but a guard that matched EVERYTHING would refuse nothing
    and quietly delete the observation channel. These four are the canonical
    comment-prose findings process-fixes AC-001 names; the guard must stay
    silent on all of them, or the refusal above it can never fire again."""
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
    """process-fixes OT-002 / AC-002 — a finding matching BOTH a denylist entry
    and an observation class is a DEFECT. The denylist outranks the observation
    class, so the filing succeeds rather than being refused."""
    result = _file_defect(
        cycle=1,
        source="assay",
        defect_type="WRONG",
        target_kind="comment",
        project_root=str(tmp_path),
        **kwargs,
    )
    assert "error" not in result, result
    assert result["defect_id"] == "D-001"


# --- process-fixes AC-002 / OT-002 ------------------------------------------
def test_security_claim_cannot_be_demoted_and_fires_tripwire(
    run: Path, tmp_path: Path
) -> None:
    """process-fixes AC-002 — a security-property claim can never be recorded
    as an observation; the attempt is rejected and the audit tripwire fires."""
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
    """process-fixes AC-002 — a spec-required-behaviour claim can never be an
    observation. A non-empty spec_ref IS such a claim, so citing a requirement
    is by itself enough to keep a finding in the blocking channel."""
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
    """process-fixes AC-002 — "anything non-comment" can never be an
    observation, and an UNDECLARED subject cannot be shown to be a comment
    either. Both are rejected under the NON_COMMENT entry."""
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
    """D-069 / process-fixes AC-002 — the demotion path fails CLOSED when the
    argument is not passed AT ALL, not merely when it is passed empty.

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


# --- process-fixes CT-002 / AC-019 / OT-008 ---------------------------------
def test_partial_defect_type_is_accepted_and_stored_verbatim(
    run: Path, tmp_path: Path
) -> None:
    """process-fixes AC-019 / OT-008 — Foundry-Defect accepts PARTIAL and
    stores it verbatim."""
    result = _file_defect(
        cycle=1,
        source="flow_trace",
        defect_type="PARTIAL",
        description="Packet produces only half its declared outputs.",
        project_root=str(tmp_path),
    )
    assert "error" not in result, result
    record = _defects(run)[0]
    assert record["type"] == "PARTIAL"
    # process-fixes AC-019 — source attribution is preserved verbatim, not
    # coerced.
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

    This is normalisation, not the coercion process-fixes CT-002 forbids:
    that rule governs UNKNOWN values, which the membership check rejects by
    name."""
    for i, spelling in enumerate(("MISPLACED", "ARCHITECTURAL_PLACEMENT")):
        result = _file_defect(
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
    """process-fixes CT-002 — server-side rejection of an unknown source,
    naming the legal set. Emphatically NOT coerced onto "trace", which is what
    the old sync path did and what made a finding show up under a stream that
    never filed it."""
    result = _file_defect(
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
    """process-fixes CT-002 — unknown defect_type is refused by name."""
    result = _file_defect(
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


def test_defect_class_is_required_and_persisted_under_the_class_key(
    run: Path, tmp_path: Path
) -> None:
    """The root-cause field is named exactly "class" — escalation keys on it.

    RENAMED, because this test's subject moved to convergence CT-002 /
    FR-007 / AC-010 — the required-class rule. It is also still the
    process-fixes CT-002 test, which is the reconciled-vocabulary rule, so this
    is the one place in the file where both CT-002s are live at once and the
    bare-means-convergence convention is worth spelling out rather than
    relying on. It used to say "the OPTIONAL root-cause field", and optional
    is what made the field worthless where it mattered: escalation counts
    consecutive cycles per declared class, so a filing without one cannot
    recur as anything — it is invisible to process-fixes ST-002 however many
    times its root cause comes back, and the path fallback that stood in for
    it survives only for READING pre-change archives.

    The persistence assertion below is unchanged. What is added is the other
    half of the same property, which is now the Locked one: omitting the field
    is a refusal naming it, and nothing is filed. Keeping only the first half
    would leave this file asserting that a class is stored when one is given
    while saying nothing about the case that used to be legal.
    """
    _file_defect(
        cycle=1,
        source="trace",
        defect_type="MISSING",
        description="third instance of the same root cause",
        defect_class="UNWIRED_DISPATCH",
        project_root=str(tmp_path),
    )
    assert _defects(run)[0]["class"] == "UNWIRED_DISPATCH"

    # The behaviour this test used to be named for: filing with no class at
    # all. Driven through the REAL door rather than the module's wrapper,
    # because the wrapper's whole job is to supply the field this half must
    # arrive without.
    refused = foundry_add_defect(
        cycle=1,
        source="trace",
        defect_type="MISSING",
        description="fourth instance of the same root cause",
        tier="LIVE",
        project_root=str(tmp_path),
    )
    assert refused["ok"] is False, refused
    assert refused["field"] == "class"
    assert "class" in refused["error"]
    assert len(_defects(run)) == 1, "the refused filing must persist nothing"


# --- process-fixes AC-025 / FR-020 ------------------------------------------
def test_concurrent_defects_get_unique_ids_and_all_survive(
    run: Path, tmp_path: Path
) -> None:
    """process-fixes AC-025 — the positional ``len(defects) + 1`` allocation
    let two simultaneous filings compute the same id, and the second .tmp
    rename discarded the first record entirely. Both properties are asserted:
    ids are unique AND no record is lost."""
    filings = 24
    barrier = threading.Barrier(filings)
    results: list[dict] = []
    lock = threading.Lock()

    def _file(n: int) -> None:
        barrier.wait(timeout=30)
        r = _file_defect(
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
    """process-fixes FR-023 — the separation is the locked part: observations
    are typed, persisted per run, and never mixed into defects.json."""
    _file_defect(
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
    _file_defect(
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


# --- query surface (process-fixes FR-023) ------------------------------------
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
    # three, not the two whose argument said 2. That is the point of
    # process-fixes ST-001: the filter and the stamp read the same counter.
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


# --- process-fixes ST-001 — the server owns the cycle number ----------------
def test_defect_is_stamped_with_the_server_cycle_not_the_callers(
    run: Path, tmp_path: Path
) -> None:
    """process-fixes ST-001 — where a server-side counter exists it is the
    authority, and a caller-supplied cycle is not trusted against it.

    grand-vulture's state.json read `"cycle": 0` for its entire life while its
    defects carried lead-asserted cycles 0-17, because every cycle number in
    the data model was an argument rather than a fact. Escalation counts
    consecutive cycles per class and the roll-up is keyed by cycle: both are
    meaningless against a number the caller picked."""
    _set_server_cycle(run, 7)
    result = _file_defect(
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
    process-fixes ST-001 exists to remove,
    ``foundry_state.py#current_cycle`` has resolved this input to 0 since
    D-059, and a filing door that disagrees with its sibling about WHICH cycle
    a record belongs to breaks escalation's
    consecutive-cycle count no matter which door is "right".

    The caller is not silently overruled — its 12 is on the record.
    """
    _drop_server_cycle(run)
    result = _file_defect(
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
    the caller's 8, while ``orchestration/fix_gate.py`` read the identical file
    and stamped 0. Identical findings filed through Foundry-Defect and
    Foundry-Sync therefore landed in different cycles: mixed filing persisted
    [1,0,3] where one door alone would have persisted [1,2,3], the longest
    consecutive run was 2, and a genuine systemic class evaded
    process-fixes ST-002 escalation while the process-fixes AC-011 DONE guard
    passed.

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

    result = _file_defect(
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
    """process-fixes NFR-002 guard on the ruling: D-119 changes the MALFORMED
    path only. A real counter is still the authority and the caller's number is
    still ignored — it is now merely recorded as well."""
    _set_server_cycle(run, 6)
    result = _file_defect(
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


# --- process-fixes AC-002 — the tripwire is reachable, not just present -----
def test_tripwire_fires_through_the_public_observation_surface(
    run: Path, tmp_path: Path
) -> None:
    """process-fixes AC-002 — a denylisted finding arriving through the
    Foundry-Observation surface is rejected AND drives the tripwire non-empty.

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
    _file_defect(
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
    result = _file_defect(
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
    _file_defect(
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

    result = _file_defect(
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

    result = _file_defect(
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
    and reporting success. ``orchestration/fix_gate.py``'s two ledger writers import
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


# --------------------------------------------------------------------------- #
# D-125 / D-127 — the persistence layer's guards derive their own membership.
#
# The class these two belong to is "a hardening mechanism bound BY HAND to the
# site where a defect was reported". D-096's container check and D-097's
# record filter were both real fixes, applied at the doors that had been bitten;
# the doors that had not been bitten stayed open, and D-127 is what came
# through them. D-103 locked every ledger writer it could find by hand, and
# D-125 is the writer it did not find.
#
# So the tests below do not enumerate doors. They derive the door set — from
# the dispatch table, from the module's call graph, from the AST — and fail
# when a member of the derived set is unbound.
# --------------------------------------------------------------------------- #

import ast  # noqa: E402
import inspect  # noqa: E402

import foundry_mcp.server as foundry_server  # noqa: E402
from foundry_mcp.tools import foundry as foundry_module  # noqa: E402
from foundry_mcp.tools.foundry import (  # noqa: E402
    LedgerShapeError,
    _dict_records,
    _locked_document,
    ledger_refusals,
    write_document,
)

_FOUNDRY_SRC = Path(inspect.getsourcefile(foundry_module))
_SERVER_SRC = Path(inspect.getsourcefile(foundry_server))

#: The locked write primitives. Anything reaching one of these is a writer of a
#: run artifact and inherits its lock; anything renaming a file into place
#: WITHOUT reaching one has found its way around the lock.
_LOCKED_PRIMITIVES = {"ledger_transaction", "_locked_document", "write_document"}


def _module_functions(tree: ast.Module) -> dict:
    return {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _callee_names(node: ast.AST) -> set:
    """Every name this function calls, by simple name or attribute."""
    names = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            if isinstance(sub.func, ast.Name):
                names.add(sub.func.id)
            elif isinstance(sub.func, ast.Attribute):
                names.add(sub.func.attr)
    return names


def _enclosing_function(tree: ast.Module, target: ast.AST) -> str | None:
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if any(node is target for node in ast.walk(fn)):
                return fn.name
    return None


def test_no_unlocked_run_artifact_write_path() -> None:
    """D-125's structural half, DERIVED FROM THE AST rather than from a list.

    ``foundry_add_verdict`` was an unlocked read-modify-write on verdicts.json
    while the orchestrator wrote the same file under a lock, so whichever
    ``.tmp`` renamed last silently discarded the other's row. Routing that one
    function through the transaction closes the instance and leaves the shape:
    the next writer added to this module reaches for the same bare atomic
    write, because it is right there and it looks correct.

    The rule is therefore about the MECHANISM, not the writer. Every
    rename-into-place in this module must sit inside a function that takes the
    ledger lock. The two sets are both derived here — write sites by walking
    for ``.rename(``, lock holders by walking for ``fcntl.flock`` — so a new
    unlocked writer is a failing test on the day it is written, and no list
    anywhere has to be remembered.
    """
    tree = ast.parse(_FOUNDRY_SRC.read_text(encoding="utf-8"))

    # Derived: which functions actually hold the lock.
    lock_holders = {
        name
        for name, node in _module_functions(tree).items()
        if "flock" in _callee_names(node)
    }
    assert lock_holders, "no function in foundry.py takes the ledger flock"

    # Derived: every rename-into-place, and the function it sits in. Two
    # signatures, because the idiom has two halves and either alone is the
    # defect: the rename itself, and the ``.tmp`` scratch file that only ever
    # exists to be renamed. Spelling the rename ``Path.replace`` instead of
    # ``Path.rename`` would slip the first check and not the second — and a
    # bare ``.replace`` cannot be matched on its own, because ``str.replace``
    # is everywhere and the AST cannot tell them apart.
    offenders = []

    def _flag(node: ast.AST) -> None:
        enclosing = _enclosing_function(tree, node)
        # The atomic writer IS the tool the lock holders use; it is reached
        # only from inside them, which the next assertion pins.
        if enclosing in lock_holders or enclosing == "_atomic_rename_write":
            return
        offenders.append(f"{enclosing}:{node.lineno}")

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.args
        ):
            receiver = node.func.value
            is_rename = node.func.attr == "rename"
            is_os_move = isinstance(receiver, ast.Name) and (
                (receiver.id == "os" and node.func.attr in {"replace", "rename"})
                or (receiver.id == "shutil" and node.func.attr == "move")
            )
            if is_rename or is_os_move:
                _flag(node)
        elif isinstance(node, ast.Constant) and node.value == ".tmp":
            _flag(node)
    assert offenders == [], (
        f"{offenders} rename a file into place outside the ledger lock. That is "
        f"D-125's shape: a second writer of a run artifact racing the locked "
        f"one, where whichever .tmp renames last discards the other's record "
        f"while both report success. Route it through ledger_transaction or "
        f"write_document."
    )

    # And the atomic writer itself is reachable ONLY from lock holders, so the
    # exemption above cannot become the new back door.
    callers = {
        name
        for name, node in _module_functions(tree).items()
        if "_atomic_rename_write" in _callee_names(node)
    }
    assert callers and callers <= lock_holders, (
        f"_atomic_rename_write is called from {sorted(callers - lock_holders)}, "
        f"which do not hold the lock"
    )


def test_every_ledger_writing_door_answers_in_band() -> None:
    """D-127's refusal-shape half, DERIVED FROM THE DISPATCH TABLE.

    The house rule is that a tool returns ``{error, hint}`` and never raises
    across the MCP boundary. A ledger whose container shape is wrong is
    discovered inside the locked primitive, frames below the entry point, and
    the old arrangement trusted each entry point to remember a pre-flight check
    for it. D-127 is what that costs: this module's doors remembered,
    ``orchestration/fix_gate.py``'s did not, and Foundry-Sync and Foundry-Fix
    surfaced ``call_tool``'s unhandled-error banner instead of a refusal.

    The member set is not typed here. It is computed: the tools ``server.py``
    actually dispatches to this module, crossed with this module's call graph
    to find which of them reach a locked transaction. Add a ledger-writing tool
    to ``_DISPATCH`` without ``@ledger_refusals`` and this fails, naming it.
    """
    server_tree = ast.parse(_SERVER_SRC.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(server_tree):
        if isinstance(node, ast.ImportFrom) and node.module == "foundry_mcp.tools.foundry":
            imported |= {a.asname or a.name for a in node.names}
    assert imported, "server.py imports nothing from tools.foundry"

    dispatch = None
    for node in ast.walk(server_tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "_DISPATCH" for t in node.targets
        ):
            dispatch = node.value
    assert isinstance(dispatch, ast.Dict), "server.py has no _DISPATCH dict"

    doors: dict[str, str] = {}
    for key, value in zip(dispatch.keys, dispatch.values):
        for sub in ast.walk(value):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id in imported
            ):
                doors[sub.func.id] = key.value

    foundry_tree = ast.parse(_FOUNDRY_SRC.read_text(encoding="utf-8"))
    functions = _module_functions(foundry_tree)
    edges = {name: _callee_names(node) for name, node in functions.items()}

    def _reaches_a_transaction(name: str, seen: set | None = None) -> bool:
        seen = seen if seen is not None else set()
        if name in seen:
            return False
        seen.add(name)
        callees = edges.get(name, set())
        if callees & _LOCKED_PRIMITIVES:
            return True
        return any(_reaches_a_transaction(c, seen) for c in callees if c in edges)

    writing_doors = {n: t for n, t in doors.items() if _reaches_a_transaction(n)}
    assert writing_doors, (
        "no dispatched tool in this module reaches a locked transaction — the "
        "derivation is broken, not the code"
    )

    unbound = sorted(
        f"{tool} -> {name}"
        for name, tool in writing_doors.items()
        if "ledger_refusals"
        not in {d.id for d in functions[name].decorator_list if isinstance(d, ast.Name)}
    )
    assert unbound == [], (
        f"{unbound} write a ledger but can raise LedgerShapeError across the "
        f"MCP boundary, where call_tool turns it into an unhandled-error "
        f"banner instead of the house refusal. Decorate with @ledger_refusals."
    )


def test_the_refusal_a_door_returns_is_the_one_the_guard_would_have_given(
    run: Path, tmp_path: Path
) -> None:
    """The decorator is only worth having if what it returns is a REFUSAL.

    Same file, same fault, reported two ways: the pre-flight guard names it
    before the tool starts, the primitive names it once the lock is held. An
    operator must not be able to tell which noticed.
    """
    from foundry_mcp.tools.foundry import (
        _named_artifact_guard,
        ledger_shape_problem,
    )

    path = run / "defects.json"
    prior = {"defects": {"D-001": {"id": "D-001"}}, "sibling": "must survive"}
    path.write_text(json.dumps(prior), encoding="utf-8")

    pre_flight = _named_artifact_guard(run, "defects.json")
    assert pre_flight is not None

    raised = None
    try:
        with ledger_transaction(path, "defects") as records:
            records.append({"id": "D-002"})
    except LedgerShapeError as exc:
        raised = exc
    assert raised is not None, "the primitive must still fail CLOSED"
    assert raised.refusal == pre_flight, (
        f"the two refusals disagree:\n  guard:     {pre_flight}\n"
        f"  primitive: {raised.refusal}"
    )
    assert "error" in raised.refusal and "hint" in raised.refusal
    assert ledger_shape_problem(path, "defects") in raised.refusal["error"]
    # Fails closed: the file is untouched.
    assert json.loads(path.read_text(encoding="utf-8")) == prior


def test_the_scoped_guard_and_the_leafs_no_longer_share_a_name() -> None:
    """fallout FR-008 / GI-024 (D-061) — ONE definition of ``_artifact_guard``.

    The guard the test above drives used to be called ``_artifact_guard``, and
    so is ``tools/artifacts.py``'s — a different function reachable under the
    same name. Every sweep keyed on names read that as one rule copied twice,
    and because neither copy could go (deleting either breaks the other's call
    sites on ARITY, and folding this one's ledger rung into the leaf is what
    fallout GI-033 forbids) the finding could only ever be ACCOUNTED for, in
    two tables in two other modules. Renaming is the exit that closes it.

    THE PIN IS ON THE PACKAGE, not on this module: the duplication was never
    visible from either file alone, which is exactly how it survived. Asserted
    over every shipped module so a third definition appearing anywhere fails
    here rather than in a table that has to be maintained to notice.
    """
    import ast
    import inspect

    from foundry_mcp.tools import artifacts
    from foundry_mcp.tools import foundry as foundry_module
    from foundry_mcp.tools.foundry import _named_artifact_guard

    package = Path(artifacts.__file__).resolve().parent.parent

    def defines(path: Path) -> set[str]:
        """Top-level ``def``/``class`` names. An import is not a definition."""
        tree = ast.parse(path.read_text(encoding="utf-8"))
        return {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        }

    shipped = [
        path
        for path in package.rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    assert len(shipped) >= 15, [str(p) for p in shipped]

    homes = sorted(
        path.relative_to(package).as_posix()
        for path in shipped
        if "_artifact_guard" in defines(path)
    )
    assert homes == ["tools/artifacts.py"], (
        f"`_artifact_guard` is defined in {homes}. One name over two functions "
        "is what D-061 closed; a second definition re-opens it, and no "
        "name-keyed sweep can tell the two contracts apart."
    )

    # ...and the scoped guard is still here under its own name, still taking
    # the ``*names`` that make it a different rule rather than a copy. The
    # arity is not decoration: it is why neither function could be deleted for
    # the other, and a signature that lost it would mean the rename had folded
    # two contracts into one instead of separating them.
    scoped = defines(Path(foundry_module.__file__).resolve())
    assert "_named_artifact_guard" in scoped
    assert "_artifact_guard" not in scoped
    kinds = [
        p.kind for p in inspect.signature(_named_artifact_guard).parameters.values()
    ]
    assert inspect.Parameter.VAR_POSITIONAL in kinds, kinds
    leaf_kinds = [
        p.kind for p in inspect.signature(artifacts._artifact_guard).parameters.values()
    ]
    assert inspect.Parameter.VAR_POSITIONAL not in leaf_kinds, leaf_kinds


def test_ledger_transaction_yields_only_mapping_records(run: Path) -> None:
    """D-127 / D-097 at the primitive.

    ``_dict_records`` used to be applied at the call sites that had been bitten
    by a malformed record. ``foundry_sync_defects`` scans with a bare
    ``d.get("status")`` and ``d["id"]`` and had not been, so one junk record
    raised mid-transaction — after the append, so nothing was written and the
    stream's filing was gone. A caller cannot be bitten by a record it is never
    handed.
    """
    path = run / "defects.json"
    path.write_text(
        json.dumps({"defects": ["junk", {"id": "D-001"}, None, 42, {"id": "D-002"}]}),
        encoding="utf-8",
    )

    with ledger_transaction(path, "defects") as records:
        assert all(isinstance(r, dict) for r in records), records
        # The scan shape foundry_sync_defects uses, which used to raise here.
        assert [r.get("id") for r in records] == ["D-001", "D-002"]
        records.append({"id": "D-003"})


def test_the_dict_only_filter_loses_no_record_and_no_position(run: Path) -> None:
    """process-fixes NFR-002 against the fix itself.

    A filter that DROPPED the records it hides would be a quieter D-096:
    refusing to lose records to a bad container while losing them to a bad
    record. They are set aside by index and re-inserted before the write, so
    the ledger comes back off the lock byte-for-byte as it went in, plus the
    append.
    """
    path = run / "defects.json"
    original = ["junk-first", {"id": "D-001"}, None, {"id": "D-002"}, 42]
    path.write_text(json.dumps({"defects": original, "sibling": "kept"}), encoding="utf-8")

    with ledger_transaction(path, "defects") as records:
        records.append({"id": "D-003"})

    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["sibling"] == "kept"
    assert stored["defects"] == [
        "junk-first", {"id": "D-001"}, None, {"id": "D-002"}, 42, {"id": "D-003"},
    ], stored["defects"]


def test_the_filter_survives_a_nested_transaction(run: Path) -> None:
    """D-099's nesting crossed with D-127's filter. The inner frame must see the
    already-cleaned list and restore nothing, or the outer frame's set-aside
    records would be re-inserted twice."""
    path = run / "observations.json"
    path.write_text(
        json.dumps({"observations": ["junk", {"id": "O-001"}], "tripwire": []}),
        encoding="utf-8",
    )

    with ledger_transaction(path, "observations") as outer:
        assert all(isinstance(r, dict) for r in outer)
        with ledger_transaction(path, "observations") as inner:
            assert inner is outer
            inner.append({"id": "O-002"})

    stored = json.loads(path.read_text(encoding="utf-8"))["observations"]
    assert stored == ["junk", {"id": "O-001"}, {"id": "O-002"}], stored


def test_dict_records_stays_exported_for_out_of_module_scans() -> None:
    """The primitive's filter is the ONE home of this tolerance, and callers
    outside this module scan ledgers the primitive never handed them (roll-ups,
    reconciliation passes). Renaming or privatising it would push each of them
    back to an inline ``isinstance``, which is the copy-per-site this whole
    class is made of."""
    assert _dict_records([{"a": 1}, "junk", None, {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert _dict_records([]) == []


def test_concurrent_verdicts_all_survive(run: Path, tmp_path: Path) -> None:
    """D-125 / process-fixes AC-025 for verdicts.json, the last shared run
    artifact whose read-modify-write window was still open.

    ``foundry_add_verdict`` loaded, mutated and saved as three separate steps
    with no lock at all, so two callers landing between one another's read and
    write both wrote a full document and the second rename discarded the
    first's row — while both returned success.
    """
    filings = 16
    barrier = threading.Barrier(filings)
    results: list[dict] = []
    guard = threading.Lock()

    def _record(n: int) -> None:
        barrier.wait(timeout=30)
        r = foundry_add_verdict(
            requirement_id=f"FR-{n:03d}",
            verdict="VERIFIED",
            evidence=f"concurrent verdict {n}",
            project_root=str(tmp_path),
        )
        with guard:
            results.append(r)

    threads = [threading.Thread(target=_record, args=(n,)) for n in range(filings)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(results) == filings
    persisted = json.loads((run / "verdicts.json").read_text(encoding="utf-8"))
    rows = persisted["requirements"]
    assert len(rows) == filings, "a concurrent verdict was lost"
    assert {r["id"] for r in rows} == {f"FR-{n:03d}" for n in range(filings)}
    assert {r["evidence"] for r in rows} == {
        f"concurrent verdict {n}" for n in range(filings)
    }


def test_a_verdict_and_a_peer_writer_do_not_discard_each_other(
    run: Path, tmp_path: Path
) -> None:
    """The D-125 pairing exactly as it occurs in a run.

    ``orchestration/gates.py#_synthesize_clean_prove_verdicts`` writes
    verdicts.json through the locked transaction; ``foundry_add_verdict`` did
    not. One writer holding the lock is not a lock — it is a coincidence — so
    an F4 auto-VERIFY synthesis interleaving with a real Foundry-Verdict call
    lost whichever row renamed last. This drives both writers at once through
    the surfaces they actually use.
    """
    verdicts_path = run / "verdicts.json"
    rounds = 12
    barrier = threading.Barrier(2 * rounds)

    def _tool(n: int) -> None:
        barrier.wait(timeout=30)
        foundry_add_verdict(
            requirement_id=f"AC-{n:03d}", verdict="VERIFIED",
            evidence="filed through the tool", project_root=str(tmp_path),
        )

    def _synthesizer(n: int) -> None:
        barrier.wait(timeout=30)
        with ledger_transaction(verdicts_path, "requirements") as requirements:
            requirements.append(
                {"id": f"US-{n:03d}", "verdict": "VERIFIED",
                 "evidence": "synthesized by the peer"}
            )

    threads = [threading.Thread(target=_tool, args=(n,)) for n in range(rounds)]
    threads += [threading.Thread(target=_synthesizer, args=(n,)) for n in range(rounds)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    rows = json.loads(verdicts_path.read_text(encoding="utf-8"))["requirements"]
    ids = {r["id"] for r in rows}
    missing = (
        {f"AC-{n:03d}" for n in range(rounds)} | {f"US-{n:03d}" for n in range(rounds)}
    ) - ids
    assert missing == set(), f"rows discarded by the peer's rename: {sorted(missing)}"


def test_a_seeded_artifact_is_written_under_the_same_lock(run: Path) -> None:
    """``write_document`` is the seeding counterpart, not an exemption.

    ``foundry_init`` genuinely means "replace, whatever is there" — it is also
    the repair path for a corrupt artifact, so it must NOT read first. What it
    must still do is hold the lock, because "unlocked because this writer does
    not read" is how a second writer ends up racing a first.
    """
    path = run / "verdicts.json"
    write_document(path, {"cycle": 0, "requirements": [], "seeded": True})
    assert json.loads(path.read_text(encoding="utf-8"))["seeded"] is True

    # Re-entrant with an open transaction: one document, one write at the
    # outermost exit, rather than a second write racing it.
    with _locked_document(path) as document:
        write_document(path, {"cycle": 7, "requirements": []})
        assert document["cycle"] == 7
    assert json.loads(path.read_text(encoding="utf-8"))["cycle"] == 7


def test_seeding_re_writes_a_corrupt_artifact_the_transaction_would_refuse(
    run: Path,
) -> None:
    """The reason seeding does not read, asserted as the contrast it exists for.

    ``foundry_init`` is the repair path an operator reaches for when an
    artifact is corrupt. Routing its writes through the READING primitive would
    make a corrupt ledger refuse the one call that would have replaced it — the
    way out, closed by the guard meant to protect them. So the same file, in
    the same state, must refuse one primitive and accept the other.
    """
    path = run / "verdicts.json"
    path.write_text("{ not json at all", encoding="utf-8")

    with pytest.raises(LedgerShapeError):
        with _locked_document(path):
            pass  # pragma: no cover - the enter is what refuses

    write_document(path, {"cycle": 0, "requirements": []})
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "cycle": 0, "requirements": [],
    }
    # And having been repaired, it is readable again.
    with _locked_document(path) as document:
        assert document["requirements"] == []


def test_ledger_refusals_converts_only_the_shape_error() -> None:
    """The decorator is a translation, not a swallow. Anything that is not a
    ledger-shape refusal must still propagate, or a real bug would come back as
    a tidy dict and be read as a refusal."""

    @ledger_refusals
    def _shape() -> dict:
        raise LedgerShapeError("defects.json has a 'defects' key holding dict")

    @ledger_refusals
    def _bug() -> dict:
        raise ZeroDivisionError("a real bug")

    refusal = _shape()
    assert "error" in refusal and "hint" in refusal
    with pytest.raises(ZeroDivisionError):
        _bug()



# --------------------------------------------------------------------------- #
# D-178 — THE TWO-SPEC ID CONVENTION IS PINNED, NOT MERELY DOCUMENTED.
#
# THE MACHINERY MOVED, and this is a shim. It lives in
# tests/test_spec_id_convention.py now, together with the roster pin that
# applies it to every module in this directory, and that module's banner
# carries the full history and the two reasons the per-file pins did not hold.
# Not repeated here: two copies of one explanation drift exactly as two copies
# of one scan do, which is the whole argument the move rests on.
#
# WHY THE SHIM STAYS RATHER THAN THE CALL SITES MOVING. tests/test_escalation.py
# is casting 3's and imports these four private names by hand —
# `_prose_blocks`, `_unqualified_ids`, `_TWO_SPEC_ID_RE`, and
# `_CONVERGENCE_IDS`, which it monkeypatches ON THIS MODULE before calling the
# other two. Deleting them here to tidy up would turn its pin into a collection
# error in the same cycle that was supposed to make the convention stick.
#
# WHY `_unqualified_ids` IS A FUNCTION AND NOT A RE-EXPORT. A re-exported
# function's globals are the module that DEFINED it, so
# `monkeypatch.setattr(shared, "_CONVERGENCE_IDS", ...)` would set a name
# nothing reads: casting 3's pin would go on scanning against this file's
# declared ids instead of its own, stay green, and check the wrong thing. The
# indirection is load-bearing; `test_the_shim_reads_this_modules_own_data` below
# is what keeps someone from "simplifying" it away.
#
# WHAT THE SENTINEL LINE ABOVE STILL DOES. `prose_blocks` keys its exemption off
# it: a module carrying the sentinel is one that EXPLAINS the collision, so its
# docstring and this comment block are free to name both sides of it. The shim
# passes `require_sentinel=True`, so deleting the line raises instead of quietly
# widening what this file scans.
# --------------------------------------------------------------------------- #

from tests.test_spec_id_convention import (  # noqa: E402
    LEGACY_ID_FAMILIES,
    id_pattern,
    prose_blocks,
    unqualified_ids,
)


def _own_source() -> str:
    return Path(__file__).read_text(encoding="utf-8")


#: The requirement-id families this module's pin matches. One deliberate step
#: narrower than the roster pin's: tests/test_escalation.py runs its own pin
#: through this shim and cites three bare `GI` ids, so widening here would turn
#: a green pin red in a file this casting may not edit.
_TWO_SPEC_ID_RE = id_pattern(LEGACY_ID_FAMILIES)

#: The convergence-spec ids this module cites BARE. EMPTY now, because the
#: convention became total and every id in this module's prose carries its spec.
#: The name survives only because tests/test_escalation.py monkeypatches it here
#: with its own declared set before calling `_unqualified_ids`.
_CONVERGENCE_IDS = frozenset()  # 0 items



def _unqualified_ids(text: str) -> list[str]:
    """`unqualified_ids` bound to whatever data THIS module declares.

    The `_CONVERGENCE_IDS` lookup is deliberately a module-global read at call
    time rather than a default argument captured at definition time, so
    casting 3's `monkeypatch.setattr` on this module reaches it.
    """
    return unqualified_ids(text, bare_ok=_CONVERGENCE_IDS, families=LEGACY_ID_FAMILIES)


def _prose_blocks(source: str) -> list[tuple[int, str]]:
    """`prose_blocks` with the sentinel deletion guard armed."""
    return prose_blocks(source, require_sentinel=True)


def test_every_requirement_id_in_this_module_names_its_spec() -> None:
    """D-178's root cause, refused structurally rather than re-tagged by hand.

    Held here as well as by the roster pin in tests/test_spec_id_convention.py,
    because this is the pair casting 3's own pin runs through: if the shim ever
    stops reporting what it used to, it fails HERE, on this module's own prose,
    rather than silently in a file this casting cannot edit.
    """
    offenders: list[str] = []
    for lineno, text in _prose_blocks(_own_source()):
        for offence in _unqualified_ids(text):
            offenders.append(f"line {lineno}: {offence}")

    assert not offenders, (
        "unqualified requirement id(s) — D-178 again. Every id in prose names "
        "its spec: 'process-fixes AC-001' for "
        "forge-specs/foundry-run-process-fixes, 'convergence AC-001' for "
        "forge-specs/foundry-run-convergence. There is no bare form:\n  "
        + "\n  ".join(offenders)
    )


def test_the_pin_catches_the_bare_tag_it_was_written_for() -> None:
    """The pin's own fail-safe: a guard that cannot fail guards nothing.

    D-093's lesson one rung over — the comment-prose battery stayed green for
    eleven cycles while eight of its ten cases were unreachable, because it was
    only ever pinned where it already worked. So the scan is driven directly
    over the tag D-178 was filed against, which must be reported, and over each
    legal form, which must not be.
    """
    # The shapes D-178 was filed against, verbatim from the old prose.
    assert _unqualified_ids("AC-001 / OT-001 comment-prose filed as a defect")
    assert _unqualified_ids("AC-001 — each of the four comment-prose classes")
    assert _unqualified_ids("ST-001 — the server owns the cycle number")
    # A chain whose HEAD is unqualified is not rescued by its own tail.
    assert _unqualified_ids("CT-002 / AC-019 / OT-008 — accepts PARTIAL")

    # The legal forms stay silent, including across a `/` chain and a wrap.
    assert not _unqualified_ids("process-fixes AC-001 / OT-001 is refused")
    assert not _unqualified_ids("process-fixes CT-002 / AC-019 / OT-008 — PARTIAL")
    assert not _unqualified_ids("evaded process-fixes ST-002 escalation while")
    # The convergence half of the total convention, which is what replaced the
    # bare form this line used to assert. `_CONVERGENCE_IDS` is empty here now,
    # so nothing is silent by declaration any more — only by qualification.
    assert not _unqualified_ids("``tier`` (convergence CT-001 / FR-004 / AC-006)")
    assert _unqualified_ids("``tier`` (CT-001 / FR-004 / AC-006) and ``class``")


def test_the_prose_scan_sees_comments_and_docstrings_alike() -> None:
    """Both carriers, because D-178 lived in both.

    The filed instance was a module docstring AND a section-header comment. A
    scan that read only one of them would have closed half the defect and left
    the other half to be re-filed.
    """
    blocks = _prose_blocks(_own_source())
    joined = " ".join(text for _, text in blocks)
    # A docstring this file owns.
    assert "each of the four comment-prose classes is refused" in joined
    # A comment this file owns.
    assert "it never reaches defects.json" in joined
    # And the two exempt blocks are absent: the legend and this pin's rationale.
    assert "READ THIS BEFORE GREPPING AN ID HERE" not in joined
    assert "THE TWO-SPEC ID CONVENTION IS PINNED" not in joined


def test_the_shim_reads_this_modules_own_data(monkeypatch) -> None:
    """The seam tests/test_escalation.py hangs on, pinned from this side.

    That module patches ``_CONVERGENCE_IDS`` ON THIS MODULE and then calls
    ``_unqualified_ids`` here, so the lookup has to happen in this namespace at
    call time. Turn the shim into a plain re-export — the obvious tidy-up —
    and the patch sets a name nothing reads: casting 3's pin would keep
    scanning against whatever set this file declares, stay green, and be
    checking the wrong module's ids. Green and wrong is the failure mode that
    has no symptom, so it is pinned here rather than left to be noticed.
    """
    assert _CONVERGENCE_IDS == frozenset()
    assert _unqualified_ids("AC-004 verbatim: 'escalation.json records'")

    monkeypatch.setattr(
        "tests.test_observations._CONVERGENCE_IDS", frozenset({"AC-004"})
    )
    assert not _unqualified_ids("AC-004 verbatim: 'escalation.json records'")

    # The family stays narrow through the shim whatever the patch says, which
    # is the other half of what keeps casting 3's file green: its three bare
    # ``GI`` citations are outside LEGACY_ID_FAMILIES and stay unreported here.
    assert not _unqualified_ids("GI-006 requires the generated report")
    assert unqualified_ids("GI-006 requires the generated report")


# ---------------------------------------------------------------------------
# fallout CT-017 / FR-017 / GI-027 — TEMPER_CANDIDATE, the fifth observation
# class and the one that is not about comment prose at all.
# ---------------------------------------------------------------------------


def test_a_temper_candidate_is_accepted_and_read_back(run: Path, tmp_path: Path) -> None:
    """fallout CT-017 / OT-022 verbatim: 'A TEMPER_CANDIDATE observation is
    accepted, TEMPER reads it first, and an undriven candidate is listed in the
    F6 report.'

    The first two clauses, driven end to end: recorded at the door, then read
    back through the query TEMPER uses,
    ``Foundry-Observations(classification=TEMPER_CANDIDATE)``.

    ``target_kind="code"`` IS THE POINT. A candidate is a probe idea about the
    implementation — vocab's own block calls it 'the one that is not about
    comment prose at all', and casting 10's report fixture seeds one with
    exactly this subject. Every other class in this ledger requires
    ``target_kind="comment"`` because recording one IS a demotion of a
    comment-prose finding; nothing has been shown to be wrong here, so there is
    no defect for the ledger to hide and the subject rung does not apply.
    """
    result = foundry_add_observation(
        cycle=1,
        source="prove",
        description=(
            "nobody has driven what the reaper does to a session the purge "
            "cascade is halfway through"
        ),
        classification="TEMPER_CANDIDATE",
        target_kind="code",
        symbol="reap_expired",
        file_path="src/session/reaper.py",
        project_root=str(tmp_path),
    )

    assert result.get("observation_id"), result
    assert result["classification"] == "TEMPER_CANDIDATE", result

    read_back = foundry_query_observations(
        classification="TEMPER_CANDIDATE", project_root=str(tmp_path)
    )
    assert [o["id"] for o in read_back["observations"]] == [result["observation_id"]]
    assert read_back["observations"][0]["target_kind"] == "code"
    # No demotion was attempted, so no audit signal fired.
    assert read_back["tripwire"] == [], read_back
    assert _defects(run) == [], "a candidate is not a defect"


def test_an_undeclared_candidate_still_meets_the_comment_subject_rung(
    run: Path, tmp_path: Path
) -> None:
    """The narrowing, asserted from the side that must NOT move.

    The exemption is keyed on the classification the caller DECLARED. An
    omitted ``classification`` reaches the comment-subject rung exactly as it
    did before this casting — which is process-fixes D-069's ruling applied
    rather than re-argued: a default that decides this question for a caller
    who said nothing is how a fabricated ``target_kind="comment"`` got into the
    ledger in the first place. ``observation_class`` walks four comment-prose
    predicates and can never answer TEMPER_CANDIDATE, so there is no path by
    which the exemption is reached without somebody asking for it.
    """
    refusal = foundry_add_observation(
        cycle=1,
        source="prove",
        description=(
            "nobody has driven what the reaper does to a session the purge "
            "cascade is halfway through"
        ),
        target_kind="code",
        project_root=str(tmp_path),
    )

    assert refusal.get("denylist_class") == "NON_COMMENT", refusal
    assert len(_observations(run)["tripwire"]) == 1, _observations(run)
    assert _observations(run)["observations"] == []


def test_a_candidate_that_claims_a_security_property_is_still_refused(
    run: Path, tmp_path: Path
) -> None:
    """fallout GI-004: 'The never-demote denylist ... is UNCHANGED by the fifth
    member and still outranks every one of them ... a probe idea whose
    description makes a security-property claim is a DEFECT, and the tripwire
    fires naming the entry that matched.'

    The exemption lifts ONE denylist entry — NON_COMMENT, which reads the
    finding's subject — and leaves the three that read what the finding CLAIMS
    exactly where they were. A tier or a class is never a route around the
    never-weaken guarantee, and this is the test that fails if the exemption is
    ever widened from 'the subject rung' to 'the denylist'.
    """
    from foundry_mcp.schemas.vocab import SECURITY_PROPERTY_CLAIM

    refusal = foundry_add_observation(
        cycle=1,
        source="prove",
        description=(
            "the login endpoint does not verify the authentication token "
            "signature"
        ),
        classification="TEMPER_CANDIDATE",
        target_kind="code",
        project_root=str(tmp_path),
    )

    assert refusal.get("denylist_class") == SECURITY_PROPERTY_CLAIM, refusal
    fired = _observations(run)["tripwire"]
    assert len(fired) == 1 and fired[0]["denylist_class"] == SECURITY_PROPERTY_CLAIM
    assert _observations(run)["observations"] == []


def test_a_classification_outside_the_vocabulary_is_refused_naming_the_set(
    run: Path, tmp_path: Path
) -> None:
    """fallout CT-017's one error: 'classification not in OBSERVATION_CLASSES'.

    The refusal names the set by DERIVING it from the constant rather than by
    listing it, which is this package's third convention — so the sentence an
    operator reads gained TEMPER_CANDIDATE the moment casting 10 added the
    member, with no edit here. Asserted against the live frozenset, not against
    a copy of its members typed into this file, since a hand-typed expectation
    would agree with a hand-typed hint and prove nothing.
    """
    from foundry_mcp.schemas.vocab import OBSERVATION_CLASSES

    refusal = foundry_add_observation(
        cycle=1,
        source="prove",
        description="the comment above the guard still says line 244",
        classification="TEMPER_CANDIDATES",  # one letter off a real member
        target_kind="comment",
        project_root=str(tmp_path),
    )

    assert "Invalid classification" in refusal["error"], refusal
    for member in OBSERVATION_CLASSES:
        assert member in refusal["error"], (member, refusal["error"])
    assert _observations(run)["observations"] == []
# --------------------------------------------------------------------------- #
# fallout ST-007 / fallout AC-019 / fallout AC-020 — DRIVING a candidate, the
# write half of the transition. Nothing in the package could reach the terminal
# state before this door: the create door above OPENS a candidate and the F6
# report reader splits open from driven, and between the two there was no
# writer at all — so the driven side of that split was unreachable and every
# candidate was undriven by construction.
# --------------------------------------------------------------------------- #

#: One probe idea, shared by the tests below so the SUBJECT never varies while
#: the closure does. Code, not comment prose: a candidate is the one class
#: whose subject is the implementation.
CANDIDATE_PROBE = (
    "nobody has driven what the reaper does to a session the purge cascade is "
    "halfway through"
)


def _open_candidate(tmp_path: Path, description: str = CANDIDATE_PROBE) -> str:
    """Record one TEMPER_CANDIDATE through the real door; return its id."""
    result = foundry_add_observation(
        cycle=1,
        source="prove",
        description=description,
        classification="TEMPER_CANDIDATE",
        target_kind="code",
        symbol="reap_expired",
        file_path="src/session/reaper.py",
        project_root=str(tmp_path),
    )
    assert result.get("observation_id"), result
    return result["observation_id"]


def _report_reader(fdir: Path) -> dict:
    """The F6 section reader, driven over the ledger these doors wrote.

    Imported at call time rather than at module top because it is the OTHER
    side of the boundary this section is about: the assertion is that two
    modules agree about one record, and reaching for it here rather than in the
    import block says so.
    """
    from foundry_mcp.tools.foundry_report import _read_undriven_temper_candidates

    payload, problem = _read_undriven_temper_candidates(fdir)
    assert problem is None, problem
    return payload


def test_a_driven_candidate_leaves_the_report_readers_undriven_list(
    run: Path, tmp_path: Path
) -> None:
    """fallout ST-007 verbatim: 'TEMPER_CANDIDATE observation open' ->
    'DRIVEN (filed or clean)'; and fallout AC-020, 'the F6 report lists every
    TEMPER candidate that was not driven'.

    Driven ACROSS THE MODULE BOUNDARY rather than against this door's own
    return value, because the two halves of fallout ST-007 live in two files:
    the writer here and the reader at
    ``foundry_mcp/tools/foundry_report.py#_read_undriven_temper_candidates``,
    which counts a record driven when it carries ``driven`` truthy OR
    ``status`` "DRIVEN". A door that wrote a THIRD spelling would return a
    perfectly good result and move nothing in the report — which is the exact
    shape of what this closes: a state machine whose terminal state no writer
    could reach, so the undriven section listed every candidate on every run
    and the count could never fall.

    The undriven assertion comes FIRST because it is the state every run was in
    before this door existed, and a closure test that never saw the open state
    proves the transition rather than assuming it.
    """
    candidate = _open_candidate(tmp_path)

    before = _report_reader(run)
    assert [c["id"] for c in before["candidates"]] == [candidate], before
    assert before["count"] == 1, before
    assert before["driven_count"] == 0, before

    closed = foundry_drive_temper_candidate(
        observation_id=candidate, project_root=str(tmp_path)
    )
    assert "error" not in closed, closed
    assert closed["already_driven"] is False, closed
    # The clean closure: driven, and no defect came of it.
    assert closed["driven_finding"] == "", closed

    after = _report_reader(run)
    assert after["candidates"] == [], after
    assert after["count"] == 0, after
    assert after["driven_count"] == 1, after


def test_a_candidate_driven_and_filed_records_the_defect_it_produced(
    run: Path, tmp_path: Path
) -> None:
    """fallout ST-007's parenthetical, the other closure: 'DRIVEN (filed or
    clean)' — both are closures and neither is the blank.

    ``filed`` is recorded VERBATIM and is never ranked against the defect
    ledger. fallout ST-007 admits no error on the field, and D-101 is this
    package's record of what inventing a rung a contract does not admit costs:
    the door refuses its own documented example and the stream's next move is
    to fabricate the field. So what is asserted is that the id reads back
    exactly as the stream wrote it, and that the record reaches DRIVEN by the
    same route the clean closure takes — the closure kind is a FACT on the
    record, not a second code path.
    """
    candidate = _open_candidate(tmp_path)

    closed = foundry_drive_temper_candidate(
        observation_id=candidate, filed="D-404", project_root=str(tmp_path)
    )

    assert "error" not in closed, closed
    assert closed["driven_finding"] == "D-404", closed
    assert closed["status"] == "DRIVEN", closed

    stored = _observations(run)["observations"]
    assert [o["id"] for o in stored] == [candidate], stored
    assert stored[0]["driven_finding"] == "D-404", stored
    assert stored[0]["status"] == "DRIVEN", stored
    # The record keeps everything it was FILED with: driving a candidate
    # records a result about it and rewrites nothing it said.
    assert stored[0]["classification"] == "TEMPER_CANDIDATE", stored
    assert stored[0]["description"] == CANDIDATE_PROBE, stored
    assert stored[0]["symbol"] == "reap_expired", stored
    # And it is driven as far as the report reader is concerned, exactly as the
    # clean closure is: the reader splits on driven-ness, not on outcome.
    assert _report_reader(run)["driven_count"] == 1


def test_the_query_door_reads_a_driven_candidate_back_with_its_closure(
    run: Path, tmp_path: Path
) -> None:
    """The OTHER door onto the same ledger, which the closure travels through
    untouched (fallout CT-017: 'TEMPER reads it via
    Foundry-Observations(classification=TEMPER_CANDIDATE)').

    TEMPER's roster read is the adjacent path this write has to leave working:
    a query that dropped the new keys, or a census that stopped counting a
    record once it carried a status, would leave the roster unreadable in a
    different way than the one just repaired. The summary is asserted as well
    as the records, because ``by_classification`` counts EVERY observation
    regardless of status and a driven candidate is still a candidate.
    """
    candidate = _open_candidate(tmp_path)
    foundry_drive_temper_candidate(
        observation_id=candidate, filed="D-404", project_root=str(tmp_path)
    )

    read_back = foundry_query_observations(
        classification="TEMPER_CANDIDATE", project_root=str(tmp_path)
    )

    assert [o["id"] for o in read_back["observations"]] == [candidate]
    record = read_back["observations"][0]
    assert record["status"] == "DRIVEN", record
    assert record["driven_finding"] == "D-404", record
    assert record["driven_in_cycle"] == 0, record
    assert read_back["summary"]["by_classification"] == {"TEMPER_CANDIDATE": 1}
    assert read_back["tripwire"] == [], read_back


def test_driving_an_unknown_observation_is_refused_naming_the_id(
    run: Path, tmp_path: Path
) -> None:
    """A call that closed nothing is a caller error, and it says so.

    Unlike ``supersedes`` — which rides along a filing that succeeded whatever
    it cited, so reporting the id it actually closed is the honest answer —
    the whole of this call is the closure. A success result over a record that
    does not exist would be a door reporting a transition it did not make,
    which is the class of defect the whole section exists to close.
    """
    refusal = foundry_drive_temper_candidate(
        observation_id="O-404", project_root=str(tmp_path)
    )

    assert refusal.get("field") == "observation_id", refusal
    assert "O-404" in refusal["error"], refusal
    assert "TEMPER_CANDIDATE" in refusal["hint"], refusal
    assert _observations(run)["observations"] == []


def test_driving_an_observation_that_is_not_a_candidate_is_refused(
    run: Path, tmp_path: Path
) -> None:
    """fallout ST-007's from-state is TEMPER_CANDIDATE and nothing else.

    The four comment-prose classes record a finding ABOUT prose — a statement,
    not a question — so there is nothing to drive and no closure to record. The
    refusal names the class the record actually carries, because the caller
    that reached here has an id it believed was a candidate and the useful
    thing to tell it is what that id really is.
    """
    filed = foundry_add_observation(
        cycle=1,
        source="trace",
        description=DRIFT,
        target_kind="comment",
        project_root=str(tmp_path),
    )
    observation_id = filed["observation_id"]
    assert filed["classification"] != "TEMPER_CANDIDATE", filed

    refusal = foundry_drive_temper_candidate(
        observation_id=observation_id, project_root=str(tmp_path)
    )

    assert refusal.get("field") == "classification", refusal
    assert filed["classification"] in refusal["error"], refusal
    assert "TEMPER_CANDIDATE" in refusal["error"], refusal
    # Refused, and the record is untouched -- no status, no closure.
    stored = _observations(run)["observations"][0]
    assert "status" not in stored, stored
    assert "driven_finding" not in stored, stored


def test_re_driving_a_closed_candidate_keeps_the_first_closure(
    run: Path, tmp_path: Path
) -> None:
    """Idempotent, not a refusal, and the FIRST closure is the one that stands.

    A retry after a dropped answer must not report failure over work that
    landed, so the second call succeeds and says ``already_driven``. What it
    must not do is overwrite: the stream that drove the probe first is the one
    that drove it, and a later call that rewrote the finding and the cycle
    would let a second reader silently replace the first reader's result.
    """
    candidate = _open_candidate(tmp_path)
    first = foundry_drive_temper_candidate(
        observation_id=candidate, filed="D-404", project_root=str(tmp_path)
    )
    assert first["already_driven"] is False, first

    _set_server_cycle(run, 7)
    second = foundry_drive_temper_candidate(
        observation_id=candidate, filed="D-999", project_root=str(tmp_path)
    )

    assert second["already_driven"] is True, second
    assert second["driven_finding"] == "D-404", second
    assert second["driven_in_cycle"] == 0, second

    stored = _observations(run)["observations"][0]
    assert stored["driven_finding"] == "D-404", stored
    assert stored["driven_in_cycle"] == 0, stored
    # Still exactly one driven record, not two closures on one candidate.
    assert _report_reader(run)["driven_count"] == 1
def test_a_malformed_historical_record_does_not_derail_the_closure(
    run: Path, tmp_path: Path
) -> None:
    """D-097 / D-128 at this door, driven rather than left to the AST pin.

    The transaction yields the ledger and a run carrying one junk record from
    an older writer is a run this door still has to close a candidate in. An
    element scan that assumed dicts raised MID-TRANSACTION on exactly this
    input once, and it raised after the mutation, so nothing was written and
    the caller got a traceback instead of an answer.

    NOTHING IS LOST TO THE FILTER either: the junk is set aside by index and
    re-inserted, so the ledger comes back off the lock with the closure written
    and the record it could not read still in place. Refusing to lose records
    to a bad container while losing them to a bad record is the quieter half of
    the same defect.
    """
    candidate = _open_candidate(tmp_path)

    path = run / "observations.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["observations"].insert(0, "a string where a record should be")
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    closed = foundry_drive_temper_candidate(
        observation_id=candidate, filed="D-404", project_root=str(tmp_path)
    )

    assert "error" not in closed, closed
    assert closed["already_driven"] is False, closed

    stored = _observations(run)["observations"]
    assert "a string where a record should be" in stored, stored
    record = next(o for o in stored if isinstance(o, dict))
    assert record["status"] == "DRIVEN", record
    assert record["driven_finding"] == "D-404", record
