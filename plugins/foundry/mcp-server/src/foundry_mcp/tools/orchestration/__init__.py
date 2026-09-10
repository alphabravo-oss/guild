"""The orchestration package: Foundry-Next, the gates, the transitions.

fallout FR-004 / GI-010 / AC-013 / OT-012 -- THIS FILE RE-EXPORTS NOTHING.

`tools/foundry_orchestrator.py` was one 15,639-line module and it is gone. A
package `__init__` that re-exported its symbols would be that module under a new
name: every importer would keep the spelling it had, the boundary guard would
see one node where there are thirteen, and the split would have moved code
without moving any coupling. GI-010 is one sentence -- "No facade: rewrite every
import" -- and this file is where that is either true or not.

So it is a package marker and nothing else. Every consumer names the module that
DEFINES the symbol it wants, and `tests/orchestration/test_module_boundaries.py`
asserts that this file stays empty of anything but this docstring.
"""
