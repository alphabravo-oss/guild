"""What a `key_files` entry IS, said once for every layer that asks.

fallout FR-009 (D-170, casting 7's concern C-079) — A LEAF, BECAUSE THE
PREDICATE IS READ FROM BOTH SIDES OF GI-033.

`castings/manifest.json` gives each casting a `key_files` list whose entries are
either a FILE path or a DIRECTORY, spelled with a trailing slash and covering
every path beneath it. That is not a convenience: `Foundry-Gate('cast')` caps a
casting at eight entries, so a casting carving a whole new package fits under the
cap by naming the package once — this run's own manifest carries
`.../tools/orchestration/` and `tests/orchestration/` for exactly that reason,
and F0.9 VALIDATE accepted it.

WHAT ACCEPTING IT WITHOUT SAYING IT COST. Every consumer in the package compared
the entries as bare strings, and only one — `evidence.py#_sweep_touched_castings`
— read the directory spelling correctly. D-170 measured the GRIND ownership
resolver: `Foundry-Tasks` on cycle 5 returned `owning_casting: None` for 13 of
15 tasks, seven of which belonged to casting 2 through its two directory
entries, and a lead dispatching from that field would have left nine LIVE
defects with no owner, no refusal and no warning. C-079 then found three more
instances in the same casting's files, one of which had begun to DISAGREE with
the door casting 7 had just corrected.

WHY THIS MODULE EXISTS RATHER THAN AN IMPORT. GI-033 puts `transitions.py` and
`width.py` in the VERIFIER set and `directives.py`, `foundry_spawn.py` and
`foundry_validate.py` in lifecycle/presentation, and the two layers are mutually
unreachable at module top — AC-061 refuses the lifecycle-to-verifier direction
entirely, and a verifier may import leaves and nothing else. So a verifier site
CANNOT depend on `foundry_validate.py#_key_file_covers` however convenient that
would be, and the boundary guard's own arithmetic gives the only remedy it
leaves: "a symbol read from BOTH can live only in a leaf". This is that leaf.
`orchestration/escalation.py` is the precedent for one living inside this
package: the leaf set is a CHECKED PROPERTY — imports only leaves, at any depth
— and this module imports nothing at all.

WHAT IS STILL DUPLICATED, AND WHO CLOSES IT. `foundry_validate.py` states the
same reading as `_key_file_covers` beside `KEY_FILE_DIRECTORY_SUFFIX`, and
`evidence.py#_sweep_touched_castings` states it inline. Both are other castings'
files. The names here are deliberately distinct rather than identical, because
an identical top-level name would fail the package-wide single-definition guard
in THIS casting's own files, where the guard's rule is that a row is excusable
only while it is somebody else's to close. The consolidation is one repoint each
onto this module and it is asked for in the answer to C-079; nothing here
re-decides anything `foundry_validate` decided, and a drift between the two is a
guard failure rather than a silent disagreement, because
`tests/orchestration/test_keyfiles.py` drives both bodies over one corpus.
"""
from __future__ import annotations

#: The one character that decides what a `key_files` entry IS. An entry ending
#: in it names a DIRECTORY; every other entry names a file. Spelled once so the
#: reading and the prose that quotes it cannot drift into two answers.
DIRECTORY_ENTRY_SUFFIX = "/"


def manifest_spelling(raw: object) -> str:
    """One path, spelled the way both the manifest and `git diff` spell it.

    Forward slashes, no leading `./`, no surrounding whitespace. The trailing
    slash is PRESERVED, because it is the whole of what `covers_path` reads.

    Both sources need it. `git diff --name-only` emits repo-relative paths with
    forward slashes; a manifest is hand-written and carries whatever the author
    typed, which on this run has included `./`-prefixed entries. A comparison
    that trusts the two to agree is a comparison that misses, which is the same
    class as the one this module exists to close, one character over.
    """
    text = str(raw).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def covers_path(key_file: object, path: object) -> bool:
    """Does this `key_files` entry reach `path`? (fallout FR-009 — D-170.)

    The question every consumer asks is COVERAGE, never equality. For an entry
    naming a file the two are the same question, which is why a manifest with no
    directory entry is answered exactly as it was before this module existed —
    the fix widens what matches and changes nothing that already did.

    The prefix is a SEGMENT boundary and not a string prefix, because the
    trailing slash is part of the comparison: `tools/orchestration/` covers
    `tools/orchestration/streams.py` and does NOT cover
    `tools/orchestrationXX/a.py`. An empty or whitespace-only entry covers
    NOTHING rather than everything, which is what a bare `startswith("")` would
    have done — a manifest cell nobody filled in must not silently claim the
    tree.

    Both arguments are normalised here rather than by the caller. A predicate
    that expects pre-normalised input is a predicate every caller can forget to
    prepare for, and the forgetting is silent.
    """
    key = manifest_spelling(key_file)
    if not key:
        return False
    subject = manifest_spelling(path)
    if not subject:
        return False
    if key.endswith(DIRECTORY_ENTRY_SUFFIX):
        return subject.startswith(key)
    return key == subject


def owning_entries(key_files: object, paths: object) -> list[str]:
    """Every entry in `key_files` that covers any path in `paths`, in order.

    The list form the callers that must NAME the match need — a refusal that
    says "casting 2 owns this" without saying through WHICH entry sends a lead
    looking for a line that is not in the manifest, because a covered file
    appears in nobody's `key_files` literally.
    """
    entries = key_files if isinstance(key_files, list) else []
    subjects = list(paths) if isinstance(paths, (list, tuple, set)) else []
    out: list[str] = []
    for entry in entries:
        if not isinstance(entry, str):
            continue
        if any(covers_path(entry, p) for p in subjects) and entry not in out:
            out.append(entry)
    return out
