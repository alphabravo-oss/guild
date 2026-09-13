# Frozen grammar witnesses

Real evidence-log bodies, verbatim, that
`src/foundry_mcp/tools/evidence.py#_ENVIRONMENTAL_GRAMMARS` names as the corpus
witness for a grammar. `tests/test_evidence.py#_corpus_witness_fields_by_log`
resolves a witness name against the live `evidence/` corpus AND this directory,
so a registry pointer stays resolvable whatever the run has committed.

## Why they are here and not under `evidence/`

F6 DONE strips the run's evidence corpus — main's `f1f32c5` deleted the whole
directory, all 90-odd logs — because the logs are commit-pinned run artefacts
and on main they would be re-executed against a tree that has moved past them.
So a witness pointer *into* `evidence/` resolves for the length of one run and
rots at its end, and both of this registry's prior repointings were that rot:
D-051 (four entries citing `casting-1-pytest.log`, `casting-3-observations.log`
and `casting-8-suite.log` for cycles after the tree stopped holding them) and
C-103 (two entries citing a wide blast-radius log whose own standing ruling
would delete the witness tokens).

C-103 settled what a witness wants to be: "narrow and stable". A committed
fixture is the narrowest, most stable home available — no run commit can reach
it, no strip can delete it, and a peer's evidence commit can no longer kill a
grammar's witness.

## Provenance

Each file is byte-identical to `evidence/<name>` at `57e7e1d` (`f1f32c5^`), the
last commit that held the corpus these grammars were recorded against:

    git show 57e7e1d:evidence/casting-5-both-doors.log
    git show 57e7e1d:evidence/casting-5-platform-witness.log
    git show 57e7e1d:evidence/casting-5-corpus-witness.log

Nothing here is synthesised. `witness_pair` in the registry documents each
grammar's real disagreement "taken from the cold corpus run rather than
invented", and these are the bodies that claim came from. Adding a grammar
means adding its witness body here, which is the point: a fabricated one would
have to lie about its provenance.

## They are FROZEN

Never recaptured, never edited, never re-executed. Nothing sweeps this
directory — the evidence sweep root is `<worktree>/evidence`
(`evidence.py#_sweep_one_log`'s caller), and the acceptance gate discovers
`evidence/casting-{id}-*.log` only.

Being historical records, they may name things the tree has since retired.
`casting-5-platform-witness.log`'s command embeds a `floor=78` corpus-population
lint, and `test_evidence.py` has since retired that constant as undecidable
across the strip. That is what a frozen record looks like; it is not a stale
cite to refresh. Do not edit a body to bring it up to date — the bytes are the
evidence.
