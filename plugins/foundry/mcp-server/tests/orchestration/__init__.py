"""The carved orchestration test suite.

fallout FR-005 / GI-026 / AC-014 / OT-016 — one test module per shipped module,
carved in the SAME casting as the source move. A carve landed a wave later is a
wave in which every mechanism pin points at a module that no longer exists.

The shared harness module holds the run fixture and the arrangement helpers every module here
imports BY NAME. Not `conftest.py`: conftest is not meant to be imported, and
`tests/test_evidence.py` already reaches three of this directory's scanner
helpers by name (Holmes pin-4 (a)).
"""
