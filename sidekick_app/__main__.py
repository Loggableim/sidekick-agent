"""Entry point for ``python -m sidekick_app`` / ``sidekick`` command.

Ensures the sidekick repo's own packages are found first on sys.path,
then bootstraps legacy import aliases and delegates to the real CLI.
"""
from __future__ import annotations

import sys
import os


def _ensure_self_first() -> None:
    """Insert the sidekick repo root at the front of sys.path so our own
    ``cli/`` (a package directory) shadows any stray ``cli.py`` file
    from the old ``cids-sidekick-agent`` repo that may also be on PATH."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        sys.path.remove(repo_root)
    except ValueError:
        pass
    sys.path.insert(0, repo_root)


def _bootstrap_aliases() -> None:
    """Wire up legacy import aliases before loading the CLI."""
    # Legacy package path — Python needs a real sidekick_cli/ directory
    # on disk for ``from sidekick_cli.banner import ...`` to resolve.
    # The ``sidekick_cli/__init__.py`` already does lazy forwarding to cli.*.
    aliases = {
        "sidekick_constants": "runtime._compat.shim_constants",
        "sidekick_state": "runtime._compat.shim_state",
        "sidekick_logging": "runtime._compat.shim_logging",
        "sidekick_bootstrap": "runtime._compat.shim_bootstrap",
        "sidekick_time": "runtime._compat.shim_time",
    }
    for alias, target in aliases.items():
        if alias not in sys.modules:
            try:
                mod = __import__(target, fromlist=[""])
                sys.modules[alias] = mod
            except Exception:
                pass

    # Pre-register run_agent so tools lazy-imports work
    if "run_agent" not in sys.modules:
        try:
            import importlib.util
            spec = importlib.util.find_spec("run_agent")
            if spec and spec.origin and "sidekick" in spec.origin:
                __import__("run_agent")
        except Exception:
            pass


_ensure_self_first()
_bootstrap_aliases()

# Now import the real CLI dispatcher
from cli.main import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
