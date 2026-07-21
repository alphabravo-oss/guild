#!/usr/bin/env python3
"""Phase 8 / INTENT-01 — thin shim for the intent-coverage.json validator.

The canonical, importable, wheel-safe validator body now lives at
``foundry_mcp.scripts.validate_intent_coverage`` (see GI-002 / FR-007).
This dash-named path is retained ONLY as a stable CLI entry point for
bash / agent / doc callers (agents/intent-carrier.md, commands/start.md)
and for the subprocess-based test fixtures that reference this path.

It imports ``main`` from the canonical module and delegates. When the
``foundry_mcp`` package is not installed (development / non-wheel
checkout), the ``mcp-server/src`` directory is injected onto ``sys.path``
so the import resolves. No validator logic is duplicated here — the
single source of truth is the package module.
"""
from __future__ import annotations

import sys
from pathlib import Path

try:  # Installed (uvx/pip) case — package is already importable.
    from foundry_mcp.scripts.validate_intent_coverage import main
except ModuleNotFoundError:  # Dev / non-installed checkout — add src/ to path.
    _SRC = Path(__file__).resolve().parents[1] / "mcp-server" / "src"
    if _SRC.is_dir() and str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from foundry_mcp.scripts.validate_intent_coverage import main


if __name__ == "__main__":
    sys.exit(main(sys.argv))
