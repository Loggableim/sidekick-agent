"""Standalone app package for the consolidated Sidekick repo.

Automatically sets up sys.modules aliases so old import paths
(e.g. sidekick_cli, sidekick_constants, sidekick_state, run_agent)
resolve to the new runtime/compat structure.
"""
from __future__ import annotations

import sys


def _bootstrap() -> None:
    """Wire up legacy import paths to the new monorepo structure."""
    # Only run once
    if getattr(_bootstrap, "_done", False):
        return
    _bootstrap._done = True  # type: ignore[attr-defined]

    import_paths = [
        ("sidekick_cli", "runtime._compat.shim_cli"),
        ("sidekick_constants", "runtime._compat.shim_constants"),
        ("sidekick_state", "runtime._compat.shim_state"),
        ("sidekick_logging", "runtime._compat.shim_logging"),
        ("sidekick_bootstrap", "runtime._compat.shim_bootstrap"),
        ("sidekick_time", "runtime._compat.shim_time"),
        ("hermes_constants", "runtime._compat.shim_constants"),
        ("hermes_state", "runtime._compat.shim_state"),
        ("hermes_bootstrap", "runtime._compat.shim_bootstrap"),
        ("hermes_time", "runtime._compat.shim_time"),
    ]

    for alias, target in import_paths:
        if alias not in sys.modules:
            try:
                mod = __import__(target, fromlist=[""])
                sys.modules[alias] = mod
            except Exception:
                pass

    # run_agent: import the REAL module, not a lazy stub.
    # The real run_agent.py lives at the repo root and has all the attributes
    # (AIAgent, _sidekick_home, get_tool_definitions, etc.). The compat stub
    # in runtime/_compat/run_agent.py re-exports from it, but if we set a lazy
    # empty proxy here, the WebUI crashes with
    # "module 'run_agent' has no attribute '_hermes_home'" because the proxy
    # is empty. Import the real module directly.
    if "run_agent" not in sys.modules:
        try:
            import run_agent as _real_ra  # noqa: F401
        except Exception:
            pass


_bootstrap()

# Also wire at import time
try:
    from runtime._compat import shim_cli  # noqa: F401
except Exception:
    pass
