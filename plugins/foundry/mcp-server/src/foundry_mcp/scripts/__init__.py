"""foundry_mcp.scripts — importable, wheel-safe validator modules.

Houses the canonical intent-coverage validator (validate_intent_coverage)
so Foundry-Intent-Coverage can call main() in-process rather than shelling
out to plugins/foundry/scripts/, which is not shipped in the wheel.
"""
