"""Foundry tools — defect tracking, observation recording, verdict recording,
and coverage verification.

All operations are local file reads/writes against the foundry-archive/{run}/ directory.
Zero API calls. Zero cost.

THE OBSERVATION / DEFECT SPLIT (FR-001 / FR-002 / US-001)
---------------------------------------------------------
Two ledgers, never mixed. ``defects.json`` blocks the run; ``observations.json``
does not. The rule that decides which a finding lands in is:

    DEMOTION REQUIRES AN EXPLICIT ``target_kind == "comment"``.

Both surfaces enforce that single rule, and both therefore fail in the safe
direction:

  * ``foundry_add_defect`` refuses a finding only when the caller has DECLARED
    the subject is a comment AND vocab names a comment-prose observation class
    AND no denylist entry matches. Refusing a defect is itself a demotion, so
    an undeclared subject is never refused — see the ``target_kind`` note in
    ``schemas/vocab.py``: "its absence is a caller bug, not a licence to
    demote". Without this, prose like "the handler below returns the wrong
    status" would match the loose DIRECTION_WORD regex and a real defect would
    be silently blocked.
  * ``foundry_add_observation`` rejects a finding when any denylist entry
    matches OR when the subject was not declared to be a comment, and fires the
    audit tripwire on the attempt (AC-002).

Vocabulary — every class name, stream id and defect type — comes from
``schemas/vocab.py`` and is never re-declared here.

CONCURRENCY (FR-020 / AC-025)
-----------------------------
Every ledger write goes through ``ledger_transaction``, which holds both a
process-local ``threading.RLock`` (INSPECT's 4+ parallel streams are concurrent
tool calls inside ONE MCP server process) and an ``fcntl.flock`` (separate
server processes on the same repo). Ids come from ``allocate_record_id``, which
is max-suffix+1 rather than the positional ``len+1`` that made two simultaneous
filings collide. Both are exported: the second positional site lives in
``foundry_orchestrator.foundry_sync_defects`` and must call these rather than
re-derive them.
"""

from __future__ import annotations

import fcntl
import json
import re
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from foundry_mcp.schemas.vocab import (
    DEFECT_SOURCE_IDS,
    DEFECT_TYPES,
    NEVER_DEMOTE_CLASSES,
    OBSERVATION_CLASSES,
    never_demote_class,
    observation_class,
)
from foundry_mcp.tools.foundry_state import (
    ARCHIVE_DIR,
    get_run_dir,
    set_active_run,
)
from foundry_mcp.tools.display import foundry_hammer, FOUNDRY_SEP

# ANSI colors
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_CYAN = "\033[36m"
_WHITE = "\033[37m"
_BCYAN = f"{_BOLD}{_CYAN}"
_BWHITE = f"{_BOLD}{_WHITE}"
_BGREEN = f"{_BOLD}{_GREEN}"


def _format_init_display(run_name: str, temper: bool = False, nyquist: bool = False) -> str:
    """Foundry init display with pixel-art hammer."""
    phases = [
        ("F0",   "RESEARCH", True),
        ("F0.5", "DECOMPOSE", False),
        ("F0.9", "VALIDATE", False),
        ("F1",   "CAST", False),
        ("F2",   "INSPECT", False),
        ("F3",   "GRIND", False),
        ("F4",   "ASSAY", False),
        ("F5",   "TEMPER", False),
        ("F5.5", "NYQUIST", False),
        ("F6",   "DONE", False),
    ]
    lines = [foundry_hammer(f"F O U N D R Y  {run_name}")]
    for pid, pname, active in phases:
        skip = (pid == "F5" and not temper) or (pid == "F5.5" and not nyquist)
        if active:
            icon = f"{_BGREEN}\u25b6{_RESET}"
            label = f"{_BWHITE}{pid} {pname}{_RESET}"
            right = f"{_BGREEN}\u25c0 START{_RESET}"
        elif skip:
            icon = f"{_DIM}\u2500{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = f"{_DIM}skip{_RESET}"
        else:
            icon = f"{_DIM}\u25cb{_RESET}"
            label = f"{_DIM}{pid} {pname}{_RESET}"
            right = ""
        lines.append(f"  {icon} {label}  {right}")
    lines.append(FOUNDRY_SEP)
    lines.append(f"Call {_BCYAN}Foundry-Next{_RESET} for instructions.")
    return "\n".join(lines)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, data: dict) -> None:
    """Atomic JSON write — write to .tmp then rename."""
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.rename(path)


# ---------------------------------------------------------------------------
# Locked ledger read-modify-write (FR-020 / AC-025).
#
# `_save_json` is atomic per write, but the ledger writers read, mutate and
# write as three separate steps. Two Foundry-Defect calls landing between one
# another's read and write both computed the same positional id, and the second
# `.tmp` rename discarded the first record entirely — so the race lost a
# DEFECT, not just an id. Serializing the whole read-modify-write is what makes
# "both survive" true; a non-positional id alone would not have.
# ---------------------------------------------------------------------------

# INSPECT's parallel streams are concurrent tool calls inside ONE MCP server
# process (plugin.json launches one server per project), so an flock alone
# would not serialize them — flock is advisory PER PROCESS and a second
# acquisition from the same process succeeds immediately. The RLock covers
# threads; the flock covers a second server process on the same repo.
_LEDGER_LOCK = threading.RLock()

_RECORD_ID_RE = re.compile(r"\A([A-Za-z]+)-(\d+)\Z")


def allocate_record_id(records: list, prefix: str = "D") -> str:
    """Return the next unused ``{prefix}-NNN`` id for ``records``.

    Highest-existing-suffix + 1, NOT ``len(records) + 1``: the positional form
    re-issued a live id whenever a record was removed or a prior collision left
    a duplicate, and it silently wrapped past its informal ``D-999`` ceiling.
    Width is a MINIMUM, so the sequence continues ``D-999``, ``D-1000``.

    Pure and total — a malformed or missing id contributes nothing rather than
    raising. Call it inside ``ledger_transaction`` (or any other exclusive
    section): uniqueness comes from the surrounding lock, not from this
    function.
    """
    highest = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        rid = record.get("id")
        if not isinstance(rid, str):
            continue
        m = _RECORD_ID_RE.match(rid.strip())
        if m and m.group(1) == prefix:
            highest = max(highest, int(m.group(2)))
    return f"{prefix}-{highest + 1:03d}"


@contextmanager
def ledger_transaction(path: Path, collection_key: str) -> Iterator[list]:
    """Exclusive read-modify-write over a run-artifact JSON ledger.

    Yields the ledger's record list. Mutate it in place — append, or edit
    records already in it — and the whole document is written back through
    ``_save_json``'s tmp+rename on clean exit. An exception inside the block
    propagates and NOTHING is written, so a failed classification cannot leave
    a half-updated ledger behind.

    Held locks: the module ``threading.RLock`` (re-entrant, so nesting a
    transaction inside another on the same thread is safe) and an ``fcntl``
    exclusive lock on a ``{path}.lock`` sidecar. POSIX only, which matches the
    documented macOS/Linux runtime floor.

    This is the write discipline every ledger writer must use — including
    ``foundry_orchestrator.foundry_sync_defects``, whose reopen-and-append pass
    mutates existing records and appends new ones in a single critical section.
    """
    lock_path = path.with_name(path.name + ".lock")
    with _LEDGER_LOCK:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                data = _load_json(path)
                records = data.get(collection_key)
                if not isinstance(records, list):
                    records = []
                data[collection_key] = records
                yield records
                _save_json(path, data)
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# The demotion gate (FR-001 / FR-002 / AC-001 / AC-002).
# ---------------------------------------------------------------------------


def _subject_is_declared_comment(finding: dict) -> bool:
    """True only when the caller DECLARED the finding's subject is a comment.

    ``vocab.is_non_comment`` answers a different question: it matches when
    ``target_kind`` is present and is something other than "comment", so an
    ABSENT ``target_kind`` does not match it. Absence must not license a
    demotion in either direction, which is why the positive check is explicit
    here rather than inferred from the denylist predicate.
    """
    value = finding.get("target_kind")
    return isinstance(value, str) and value.strip().lower() == "comment"


def _finding_mapping(
    description: str,
    spec_ref: str = "",
    target_kind: str = "",
) -> dict:
    """Build the mapping vocab's predicates read (see its module docstring)."""
    return {
        "description": description,
        "spec_ref": spec_ref,
        "target_kind": target_kind,
    }


def _observation_refusal(finding: dict) -> str | None:
    """Name the observation class a defect filing must be refused for, else None.

    Applies vocab's precedence rule — ``never_demote_class`` OUTRANKS
    ``observation_class``, so a finding matching both stays a DEFECT — on top
    of the explicit-declaration rule above.
    """
    if not _subject_is_declared_comment(finding):
        return None
    if never_demote_class(finding) is not None:
        return None
    return observation_class(finding)


def _ledger_mirror(fdir: Path, heading: str, fields: list[tuple[str, str]]) -> None:
    """Mirror a ledger write into forge-log.md.

    Machine-readable JSON *and* a human-readable markdown mirror, guarded by
    ``exists()`` so a missing log never fails the write.
    """
    forge_log = fdir / "forge-log.md"
    if not forge_log.exists():
        return
    with open(forge_log, "a", encoding="utf-8") as f:
        f.write(f"\n### {heading}\n")
        for label, value in fields:
            if value:
                f.write(f"- **{label}:** {value}\n")
        f.write("\n")


_ADJECTIVES = [
    "ambitious", "blazing", "bold", "brave", "calm", "clever", "cosmic",
    "daring", "deft", "eager", "fierce", "flying", "golden", "grand",
    "heroic", "humble", "iron", "jolly", "keen", "lively", "lucky",
    "mighty", "noble", "plucky", "quick", "rapid", "roaring", "sharp",
    "silver", "sleek", "soaring", "steady", "steel", "stout", "swift",
    "thunder", "titan", "valiant", "vivid", "wild", "witty", "zesty",
]

_NOUNS = [
    "anvil", "arrow", "badger", "beacon", "bison", "bolt", "canyon",
    "cedar", "comet", "condor", "crane", "falcon", "forge", "fox",
    "glacier", "granite", "hawk", "helm", "heron", "jaguar", "kestrel",
    "lance", "leopard", "lynx", "mammoth", "mantis", "maple", "marble",
    "mustang", "oak", "orca", "osprey", "otter", "panther", "phoenix",
    "pike", "puma", "quartz", "raven", "ridge", "salmon", "sequoia",
    "squid", "stallion", "summit", "talon", "tiger", "trout", "viper",
    "vulture", "walrus", "wolf", "wren",
]


# The default F0 ruling every run starts with (FR-019 / AC-004). Body text
# only — `foundry_init` wraps it in the `### [DIRECTIVE] {iso}` header.
_F0_OBSERVATION_DEFECT_RULING = (
    "OBSERVATION/DEFECT SPLIT (F0 default ruling). Comment-prose findings — a "
    "drifted line number in a cite, a count stated in prose, a direction word "
    '("above", "below", "the following"), an enumeration that no longer '
    "matches what it enumerates — are OBSERVATIONS, not defects. Record them "
    "in the run's observations.json ledger; Foundry-Defect and Foundry-Sync "
    "refuse them as defects server-side. The never-demote denylist is "
    "absolute: a security-property claim, a spec-required-behaviour claim, an "
    "unresolvable cite, and anything that is not a comment can NEVER be "
    "recorded as an observation — each stays a defect whatever else is true "
    "about it, and an attempt to demote one is rejected and fires the audit "
    "tripwire. This is a channel, not a severity tier: it buys no discretion "
    "over any behavioural or security finding. The symbol is authoritative in "
    "a cite — no verifier judges the line component, a moved line alone "
    "produces no finding of any kind, and cite-refresh sweeps require an "
    "explicit directive."
)


def _generate_run_name(ticket: str = "", description: str = "") -> str:
    """Generate a human-friendly run name.

    Uses ticket/description when available, falls back to random adjective-noun.
    Examples: 'AQUA-123-login-flow', 'fix-broken-nav', 'bold-falcon'
    """
    parts: list[str] = []
    if ticket:
        parts.append(ticket)
    if description:
        slug = description.lower().replace(" ", "-")[:40]
        slug = "".join(c for c in slug if c.isalnum() or c == "-").strip("-")
        if slug:
            parts.append(slug)
    if parts:
        return "-".join(parts)
    # Fallback: random name when no context provided
    import random
    adj = random.choice(_ADJECTIVES)
    noun = random.choice(_NOUNS)
    return f"{adj}-{noun}"


def foundry_init(
    spec_path: str | None = None,
    temper: bool = False,
    nyquist: bool = False,
    no_ui: bool = False,
    resume: str | None = None,
    ticket: str = "",
    description: str = "",
    url: str = "",
    project_root: str = ".",
) -> dict:
    """Initialize a foundry run under foundry-archive/.

    Args:
        resume: Name of existing run to resume (e.g. 'bold-falcon').
        ticket: Ticket ID (e.g., "AQUA-123") for name generation.
        description: Short description for name generation.
        url: Target URL for the SIGHT audit. Persisted to
            ``castings/manifest.json`` as ``target_url`` (the store of
            record the inspect gate readers load — foundry_orchestrator.py
            :531 / :1033), mirroring the bash init write at foundry.sh:176.
            NOT written into state.json.
        nyquist: Enable the optional F5.5 NYQUIST phase. Persisted to BOTH
            state.json and castings/manifest.json, mirroring ``temper`` and
            the bash init writes at foundry.sh:178 / :196. state.json is the
            store of record: ``_compute_next_action`` reads
            ``state["nyquist"]`` to decide whether F4/F5 transition into F5.5
            or straight to F6. Without this parameter the key was never
            written, so that read was permanently False and F5.5 was
            unreachable on the MCP path.

    Returns:
        {foundry_dir, run_name, files_created[], spec_copied}
    """
    root = Path(project_root)
    archive = root / ARCHIVE_DIR

    # --- Resume mode ---
    if resume:
        run_dir = archive / resume
        state_path = run_dir / "state.json"
        if not state_path.exists():
            return {"error": f"Run '{resume}' not found in {ARCHIVE_DIR}/"}
        set_active_run(resume)
        state = _load_json(state_path)
        return {
            "foundry_dir": str(run_dir),
            "run_name": resume,
            "resumed": True,
            "state": state,
            "display": (
                foundry_hammer(f"F O U N D R Y  Resumed: {resume}")
                + f"\n  Phase: {state.get('phase', '?')}  Cycle: {state.get('cycle', 0)}"
                + f"\n{FOUNDRY_SEP}"
                + f"\nCall Foundry-Next for instructions."
            ),
            "next_step": "Call Foundry-Next to see status and get instructions.",
        }

    # --- New run ---
    run_name = _generate_run_name(ticket=ticket, description=description)

    # Ensure unique name (don't collide with existing runs)
    archive.mkdir(parents=True, exist_ok=True)
    if (archive / run_name).exists():
        import random
        suffix = random.randint(100, 999)
        run_name = f"{run_name}-{suffix}"

    fdir = archive / run_name

    # Silently delete legacy .foundry-dir if it exists (one-time migration)
    legacy_pointer = root / ".foundry-dir"
    if legacy_pointer.exists():
        legacy_pointer.unlink(missing_ok=True)

    dirs = [
        fdir,
        fdir / "castings",
        fdir / "traces",
        fdir / "proofs",
        fdir / "proofs" / "screenshots",
    ]
    if temper:
        dirs.extend([fdir / "temper", fdir / "temper" / "probe-results"])

    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    files_created = []

    # defects.json — always fresh
    defects_path = fdir / "defects.json"
    _save_json(defects_path, {"defects": []})
    files_created.append("defects.json")

    # observations.json — always fresh. The typed non-blocking channel of
    # FR-001/FR-023, seeded alongside defects.json so a stream never has to
    # decide whether the ledger exists. `tripwire` is the durable audit signal
    # of FR-002: every rejected demotion attempt is appended there (and
    # mirrored into forge-log.md) where the lead and a validator can read it.
    observations_path = fdir / "observations.json"
    _save_json(observations_path, {"observations": [], "tripwire": []})
    files_created.append("observations.json")

    # verdicts.json — always fresh
    verdicts_path = fdir / "verdicts.json"
    _save_json(verdicts_path, {"cycle": 0, "requirements": []})
    files_created.append("verdicts.json")

    # state.json — always fresh
    state_path = fdir / "state.json"
    _init_now = datetime.now(timezone.utc).isoformat()
    state = {
        "phase": "F0",
        "cycle": 0,
        "spec_path": spec_path or "",
        "temper": temper,
        "nyquist": nyquist,
        "no_ui": no_ui,
        "started_at": _init_now,
        "phase_times": {
            # Stamp F0 start so sub-phase timing (F0 / F0.5 / F0.9) works
            # automatically; passive stamping in foundry_next_action closes
            # F0 when manifest.json appears, closes F0.5 on .validate-passed,
            # and closes F0.9 on Foundry-Phase(start_cast).
            "F0": {"started_at": _init_now},
        },
    }
    _save_json(state_path, state)
    files_created.append("state.json")

    # castings/manifest.json — the store of record for target_url.
    # The inspect gate readers (_check_streams_complete / _check_sight_required
    # in foundry_orchestrator.py:531 / :1033) load target_url from HERE, not
    # from state.json. Mirrors the bash init write shape at foundry.sh:168-182
    # so the Python and bash init paths produce byte-compatible manifests.
    # `castings: []` at init keeps the F0 guidance engine emitting DECOMPOSE
    # (it keys on len(castings), not manifest existence); decompose fills in
    # the castings/waves later. A run without a url persists target_url="",
    # which keeps the inspect gate blocked when frontend files are present.
    manifest_path = fdir / "castings" / "manifest.json"
    manifest = {
        "created_at": _init_now,
        "updated_at": _init_now,
        "spec_path": spec_path or "",
        "temper": temper,
        "nyquist": nyquist,
        "no_ui": no_ui,
        "target_url": url,
        "max_cycles": 0,
        "current_cycle": 0,
        "status": "initialized",
        "castings": [],
        "waves": [],
    }
    _save_json(manifest_path, manifest)
    files_created.append("castings/manifest.json")

    # forge-log.md — always fresh
    forge_log = fdir / "forge-log.md"
    forge_log.write_text(
        "# Forge Log\n\nCumulative record of all defects, fixes, and verdicts.\n\n---\n\n",
        encoding="utf-8",
    )
    files_created.append("forge-log.md")

    # directives.md — seeded with the F0 observation/defect ruling (FR-019 /
    # AC-004). Written in the exact grammar `_read_directives` parses (a
    # `### [DIRECTIVE] {iso}` header, a blank line, then the body), matching
    # `foundry_inject_directive` byte for byte — a body in any other shape
    # parses as no directive at all. Seeded at NORMAL priority, which is why
    # the Foundry-Next renderer must show normal directives alongside urgent
    # ones: an urgent injection must not silence this standing ruling.
    directives_path = fdir / "directives.md"
    directives_path.write_text(
        "# Foundry Directives\n\n"
        "Human steering inputs — read at every phase transition.\n\n"
        f"\n### [DIRECTIVE] {_init_now}\n\n"
        f"{_F0_OBSERVATION_DEFECT_RULING}\n",
        encoding="utf-8",
    )
    files_created.append("directives.md")

    # Copy spec
    spec_copied = False
    if spec_path:
        src = root / spec_path if not Path(spec_path).is_absolute() else Path(spec_path)
        dest = fdir / "spec.md"
        if src.exists() and not dest.exists():
            dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            spec_copied = True
            files_created.append("spec.md")

    # Set this as the active run for this session
    set_active_run(run_name)

    return {
        "foundry_dir": str(fdir),
        "run_name": run_name,
        "files_created": files_created,
        "spec_copied": spec_copied,
        "display": _format_init_display(run_name, state.get("temper", False), state.get("nyquist", False)),
        "next_step": "Call Foundry-Next to get decomposition instructions. Print the display above FIRST.",
    }


def foundry_add_defect(
    cycle: int,
    source: str,
    defect_type: str,
    description: str,
    spec_ref: str = "",
    symbol: str = "",
    file_path: str = "",
    target_kind: str = "",
    defect_class: str = "",
    project_root: str = ".",
) -> dict:
    """Add a defect to the foundry ledger.

    Args:
        source: a member of ``vocab.DEFECT_SOURCE_IDS``. Stored VERBATIM \u2014
            an unknown value is refused by name, never coerced onto "trace"
            (CT-002 / AC-019).
        defect_type: a member of ``vocab.DEFECT_TYPES``, which includes
            PARTIAL and both MISPLACED and ARCHITECTURAL_PLACEMENT. Stored
            verbatim; the alias is NOT folded, because folding is a coercion
            and CT-002 forbids coercing on this surface.
        target_kind: what the finding is ABOUT. Pass "comment" when the
            subject is a code comment \u2014 that declaration is what allows the
            comment-prose refusal of AC-001 to engage. Any other value, or
            none, means the finding is not demotable and is filed as a defect.
        defect_class: optional root-cause class shared by several instances,
            persisted under the key ``"class"``. Escalation keys on it.

    Returns:
        ``{defect_id, total_defects, open_defects}``, or a named refusal
        ``{error, hint, ...}`` when the vocabulary check or the comment-prose
        check rejects the filing.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}

    # Server-side vocabulary validation (CT-002). Only the client schema
    # guarded these before, and `foundry_sync_defects` silently rewrote an
    # unknown source to "trace" \u2014 so a finding could be attributed to a stream
    # that never filed it. Rejection replaces coercion on both surfaces.
    if source not in DEFECT_SOURCE_IDS:
        return {
            "error": (
                f"Invalid source: {source!r}. Must be one of: "
                f"{', '.join(sorted(DEFECT_SOURCE_IDS))}"
            ),
            "hint": (
                "Source is stored verbatim and is never coerced onto another "
                "stream. File under the id of the stream that actually found "
                "this, or extend schemas/vocab.py via a phase-level RFC."
            ),
        }
    if defect_type not in DEFECT_TYPES:
        return {
            "error": (
                f"Invalid defect_type: {defect_type!r}. Must be one of: "
                f"{', '.join(sorted(DEFECT_TYPES))}"
            ),
            "hint": (
                "Use the closest member of the canonical set, or extend "
                "schemas/vocab.py via a phase-level RFC."
            ),
        }

    # Comment-prose refusal (AC-001). Engages only when the caller declared
    # the subject is a comment and no denylist entry outranks the class \u2014 see
    # the module docstring for why an undeclared subject is never refused.
    finding = _finding_mapping(description, spec_ref, target_kind)
    refused_class = _observation_refusal(finding)
    if refused_class is not None:
        return {
            "error": (
                f"Refused: {refused_class} is a comment-prose observation "
                f"class, not a defect. The comment-prose classes are: "
                f"{', '.join(sorted(OBSERVATION_CLASSES))}."
            ),
            "hint": (
                "Record it in the observations ledger instead \u2014 same fields, "
                "via Foundry-Observation / foundry_add_observation. Comment "
                "prose does not block the run. If this finding actually "
                "asserts a security property, a spec-required behaviour, or "
                "an unresolvable cite, it is a defect: say so in the "
                "description or cite the requirement in spec_ref and re-file."
            ),
            "refused_class": refused_class,
            "observation_classes": sorted(OBSERVATION_CLASSES),
        }

    now = datetime.now(timezone.utc).isoformat()
    defect = {
        "id": "",  # assigned inside the transaction
        "cycle": cycle,
        "source": source,
        "type": defect_type,
        "description": description,
        "spec_ref": spec_ref,
        "symbol": symbol,
        "file": file_path,
        "status": "open",
        "fixed_in_cycle": None,
        "created_at": now,
    }
    if defect_class:
        defect["class"] = defect_class
    if target_kind:
        defect["target_kind"] = target_kind

    defects_path = fdir / "defects.json"
    with ledger_transaction(defects_path, "defects") as defects:
        defect_id = allocate_record_id(defects, "D")
        defect["id"] = defect_id
        defects.append(defect)
        total = len(defects)
        open_count = sum(1 for d in defects if d.get("status") == "open")

    _ledger_mirror(
        fdir,
        f"Cycle {cycle} \u2014 {source}: {defect_id}",
        [
            ("Type", defect_type),
            ("Class", defect_class),
            ("Description", description),
            ("Spec ref", spec_ref),
            ("Symbol", symbol),
            ("File", file_path),
        ],
    )

    return {
        "defect_id": defect_id,
        "total_defects": total,
        "open_defects": open_count,
    }


def foundry_add_observation(
    cycle: int,
    source: str,
    description: str,
    classification: str = "",
    target_kind: str = "comment",
    spec_ref: str = "",
    symbol: str = "",
    file_path: str = "",
    project_root: str = ".",
) -> dict:
    """Record a comment-prose finding in the run's observations ledger.

    The non-blocking half of the FR-001 split. Observations are typed,
    persisted per run in ``observations.json``, and NEVER mixed into
    ``defects.json``.

    The never-demote denylist is enforced here and is absolute (FR-002 /
    AC-002): a security-property claim, a spec-required-behaviour claim, an
    unresolvable cite, and anything that is not a declared comment are
    rejected, and the audit tripwire fires \u2014 durably, into the ledger's
    ``tripwire`` array and forge-log.md \u2014 naming which entry matched. A
    ``spec_ref`` is itself a spec-required-behaviour claim, so citing a
    requirement is by construction enough to keep a finding a defect.

    Args:
        classification: optional; a member of ``vocab.OBSERVATION_CLASSES``.
            Derived from the description when omitted.

    Returns:
        ``{observation_id, classification, total_observations}``, or a named
        refusal ``{error, hint, tripwire}``.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}

    if source not in DEFECT_SOURCE_IDS:
        return {
            "error": (
                f"Invalid source: {source!r}. Must be one of: "
                f"{', '.join(sorted(DEFECT_SOURCE_IDS))}"
            ),
            "hint": (
                "Observations carry the same source vocabulary as defects, "
                "and are attributed verbatim to the stream that filed them."
            ),
        }

    finding = _finding_mapping(description, spec_ref, target_kind)

    # Denylist first \u2014 vocab's precedence rule. A finding matching both a
    # denylist entry and an observation class is a DEFECT, and the denylist
    # match is what the tripwire names.
    denied = never_demote_class(finding)
    if denied is None and not _subject_is_declared_comment(finding):
        # An undeclared subject cannot be shown to be a comment, and "anything
        # non-comment" can never be an observation. Reported under the
        # existing NON_COMMENT entry rather than a new class name.
        denied = "NON_COMMENT"
    if denied is not None:
        detail = (
            f"target_kind={target_kind!r} is not a declared comment"
            if denied == "NON_COMMENT"
            else f"the finding matches the {denied} denylist entry"
        )
        tripwire = {
            "cycle": cycle,
            "source": source,
            "denylist_class": denied,
            "detail": detail,
            "description": description,
            "spec_ref": spec_ref,
            "symbol": symbol,
            "file": file_path,
            "fired_at": datetime.now(timezone.utc).isoformat(),
        }
        observations_path = fdir / "observations.json"
        with ledger_transaction(observations_path, "tripwire") as fired:
            fired.append(tripwire)
        _ledger_mirror(
            fdir,
            f"TRIPWIRE cycle {cycle} \u2014 {source}: {denied}",
            [
                ("Denylist class", denied),
                ("Detail", detail),
                ("Description", description),
                ("Spec ref", spec_ref),
                ("Symbol", symbol),
                ("File", file_path),
            ],
        )
        return {
            "error": (
                f"Refused: {denied} can NEVER be recorded as an observation \u2014 "
                f"{detail}. The never-demote denylist is "
                f"{', '.join(sorted(NEVER_DEMOTE_CLASSES))}."
            ),
            "hint": (
                "File it as a defect via Foundry-Defect. The audit tripwire "
                "has fired and is recorded in observations.json and "
                "forge-log.md."
            ),
            "denylist_class": denied,
            "tripwire": tripwire,
        }

    resolved = classification or observation_class(finding)
    if resolved is None:
        return {
            "error": (
                f"No comment-prose observation class matches this finding. "
                f"Must be one of: {', '.join(sorted(OBSERVATION_CLASSES))}"
            ),
            "hint": (
                "Only comment prose is an observation. If this finding is "
                "about behaviour, wiring, or a security property, file it as "
                "a defect via Foundry-Defect."
            ),
        }
    if resolved not in OBSERVATION_CLASSES:
        return {
            "error": (
                f"Invalid classification: {resolved!r}. Must be one of: "
                f"{', '.join(sorted(OBSERVATION_CLASSES))}"
            ),
            "hint": (
                "Use a canonical class name, or extend schemas/vocab.py via a "
                "phase-level RFC."
            ),
        }

    observation = {
        "id": "",  # assigned inside the transaction
        "cycle": cycle,
        "source": source,
        "classification": resolved,
        "description": description,
        "spec_ref": spec_ref,
        "target_kind": target_kind,
        "symbol": symbol,
        "file": file_path,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    observations_path = fdir / "observations.json"
    with ledger_transaction(observations_path, "observations") as observations:
        observation_id = allocate_record_id(observations, "O")
        observation["id"] = observation_id
        observations.append(observation)
        total = len(observations)

    _ledger_mirror(
        fdir,
        f"Cycle {cycle} \u2014 {source}: {observation_id} (observation)",
        [
            ("Classification", resolved),
            ("Description", description),
            ("Symbol", symbol),
            ("File", file_path),
        ],
    )

    return {
        "observation_id": observation_id,
        "classification": resolved,
        "total_observations": total,
    }


def foundry_query_observations(
    cycle: int | None = None,
    source: str | None = None,
    classification: str | None = None,
    project_root: str = ".",
) -> dict:
    """Query the observations ledger, with the tripwire log alongside.

    The query half of the FR-023 ledger surface. ``tripwire`` is returned
    unconditionally so a validator checking whether any stream tried to demote
    a denylisted finding never has to know the ledger's file layout.
    """
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    data = _load_json(fdir / "observations.json")
    all_observations = data.get("observations", [])
    tripwire = data.get("tripwire", [])

    observations = all_observations
    if cycle is not None:
        observations = [o for o in observations if o.get("cycle") == cycle]
    if source:
        observations = [o for o in observations if o.get("source") == source]
    if classification:
        observations = [
            o for o in observations if o.get("classification") == classification
        ]

    by_classification: dict[str, int] = {}
    by_source: dict[str, int] = {}
    for o in all_observations:
        c = o.get("classification", "unknown")
        by_classification[c] = by_classification.get(c, 0) + 1
        s = o.get("source", "unknown")
        by_source[s] = by_source.get(s, 0) + 1

    return {
        "observations": observations,
        "tripwire": tripwire,
        "summary": {
            "total": len(all_observations),
            "tripwire_fired": len(tripwire),
            "by_classification": by_classification,
            "by_source": by_source,
        },
    }


def foundry_query_defects(
    status: str | None = None,
    cycle: int | None = None,
    source: str | None = None,
    spec_ref: str | None = None,
    project_root: str = ".",
) -> dict:
    """Query defects with optional filters."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    data = _load_json(fdir / "defects.json")
    defects = data.get("defects", [])

    if status:
        defects = [d for d in defects if d.get("status") == status]
    if cycle is not None:
        defects = [d for d in defects if d.get("cycle") == cycle]
    if source:
        defects = [d for d in defects if d.get("source") == source]
    if spec_ref:
        defects = [d for d in defects if d.get("spec_ref") == spec_ref]

    all_defects = data.get("defects", [])
    by_source: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for d in all_defects:
        s = d.get("source", "unknown")
        by_source[s] = by_source.get(s, 0) + 1
        t = d.get("type", "unknown")
        by_type[t] = by_type.get(t, 0) + 1

    return {
        "defects": defects,
        "summary": {
            "total": len(all_defects),
            "open": sum(1 for d in all_defects if d.get("status") == "open"),
            "fixed": sum(1 for d in all_defects if d.get("status") == "fixed"),
            "by_source": by_source,
            "by_type": by_type,
        },
    }


def foundry_add_verdict(
    requirement_id: str,
    verdict: str,
    evidence: str,
    spec_text_cited: str = "",
    code_location: str = "",
    cycle: int = 0,
    project_root: str = ".",
) -> dict:
    """Record a verdict for a requirement with spec citation and code evidence."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    verdicts_path = fdir / "verdicts.json"
    data = _load_json(verdicts_path)
    if "requirements" not in data:
        data["requirements"] = []

    entry = {
        "id": requirement_id,
        "verdict": verdict,
        "evidence": evidence,
        "spec_text_cited": spec_text_cited,
        "code_location": code_location,
        "cycle": cycle,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }

    replaced = False
    for i, req in enumerate(data["requirements"]):
        if req.get("id") == requirement_id:
            data["requirements"][i] = entry
            replaced = True
            break
    if not replaced:
        data["requirements"].append(entry)

    data["cycle"] = cycle
    _save_json(verdicts_path, data)

    verified = sum(1 for r in data["requirements"] if r.get("verdict") == "VERIFIED")
    return {
        "requirement_id": requirement_id,
        "verdict": verdict,
        "replaced_existing": replaced,
        "total_requirements": len(data["requirements"]),
        "verified_count": verified,
    }


def foundry_verify_coverage(
    spec_path: str | None = None,
    project_root: str = ".",
) -> dict:
    """Cross-reference spec -> verdicts -> defects for full traceability."""
    fdir = get_run_dir(project_root)
    if not fdir:
        return {"error": "No active foundry run. Call Foundry-Init."}
    root = Path(project_root)

    verdicts_data = _load_json(fdir / "verdicts.json")
    requirements = verdicts_data.get("requirements", [])
    verdict_map = {r["id"]: r for r in requirements}

    defects_data = _load_json(fdir / "defects.json")
    all_defects = defects_data.get("defects", [])
    open_defects = [d for d in all_defects if d.get("status") == "open"]

    defects_by_req: dict[str, list[dict]] = {}
    for d in open_defects:
        ref = d.get("spec_ref", "")
        if ref:
            defects_by_req.setdefault(ref, []).append({
                "id": d["id"],
                "type": d.get("type", ""),
                "description": d.get("description", ""),
            })

    spec_req_ids: list[str] = []
    if spec_path:
        spath = root / spec_path if not Path(spec_path).is_absolute() else Path(spec_path)
        if spath.exists():
            import re
            spec_text = spath.read_text(encoding="utf-8")
            spec_req_ids = list(dict.fromkeys(re.findall(r"\b(US-\d+|FR-\d+|NFR-\d+)\b", spec_text)))
    elif (fdir / "spec.md").exists():
        import re
        spec_text = (fdir / "spec.md").read_text(encoding="utf-8")
        spec_req_ids = list(dict.fromkeys(re.findall(r"\b(US-\d+|FR-\d+|NFR-\d+)\b", spec_text)))

    if not spec_req_ids:
        spec_req_ids = [r["id"] for r in requirements]

    traceability = []
    gaps = []
    for req_id in spec_req_ids:
        v = verdict_map.get(req_id)
        entry = {
            "requirement_id": req_id,
            "verdict": v["verdict"] if v else None,
            "evidence": v.get("evidence", "") if v else "",
            "spec_text_cited": v.get("spec_text_cited", "") if v else "",
            "code_location": v.get("code_location", "") if v else "",
            "open_defects": defects_by_req.get(req_id, []),
            "status": "verified" if v and v["verdict"] == "VERIFIED" else (
                "non_verified" if v else "uncovered"
            ),
        }
        traceability.append(entry)
        if entry["status"] != "verified":
            gaps.append({
                "requirement_id": req_id,
                "status": entry["status"],
                "verdict": entry["verdict"],
                "open_defect_count": len(entry["open_defects"]),
            })

    verified = sum(1 for t in traceability if t["status"] == "verified")
    non_verified = sum(1 for t in traceability if t["status"] == "non_verified")
    uncovered = sum(1 for t in traceability if t["status"] == "uncovered")
    total = len(traceability)

    all_verified = verified == total and total > 0
    zero_open = len(open_defects) == 0

    return {
        "traceability": traceability,
        "coverage_summary": {
            "total_requirements": total,
            "verified": verified,
            "non_verified": non_verified,
            "uncovered": uncovered,
            "coverage_pct": f"{verified / total * 100:.0f}%" if total > 0 else "N/A",
        },
        "defect_summary": {
            "total": len(all_defects),
            "open": len(open_defects),
            "fixed": sum(1 for d in all_defects if d.get("status") == "fixed"),
        },
        "gaps": gaps,
        "pass": all_verified and zero_open,
    }
