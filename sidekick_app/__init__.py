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
        ("hermes_logging", "runtime._compat.shim_logging"),
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

    # run_agent needs a special alias — the module itself imports other things
    if "run_agent" not in sys.modules:
        try:
            # Can't eagerly import — too many side effects during bootstrap
            # Create a lazy module proxy
            import importlib.abc

            class _LazyRunAgentLoader(importlib.abc.Loader):
                def create_module(self, spec):
                    return None  # Use default semantics

                def exec_module(self, module):
                    pass  # Will be populated by the real import

            spec = importlib.machinery.ModuleSpec("run_agent", _LazyRunAgentLoader(), is_package=False)
            lazy_mod = importlib.util.module_from_spec(spec)
            sys.modules["run_agent"] = lazy_mod
        except Exception:
            pass


_bootstrap()

# Also wire at import time
try:
    from runtime._compat import shim_cli  # noqa: F401
except Exception:
    pass
