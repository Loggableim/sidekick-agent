"""
Compat shim — bridges old ``sidekick_bootstrap`` imports to the new bootstrap
structure.

The original ``sidekick_bootstrap.py`` (in ``cids-hermes-agent/``) provides a
Windows UTF-8 bootstrap function that fixes Unicode handling on Windows.
This shim re-exports that functionality.

Agent modules typically do:
  - ``import sidekick_bootstrap``  (auto-applies on import)
  - ``from sidekick_bootstrap import apply_windows_utf8_bootstrap``
"""

from __future__ import annotations

import os
import sys

_IS_WINDOWS = sys.platform == "win32"
_bootstrap_applied = False


def apply_windows_utf8_bootstrap() -> bool:
    """Apply the Windows UTF-8 bootstrap if we're on Windows.

    Returns True if bootstrap was applied, False otherwise.
    Idempotent — safe to call multiple times.

    Mirrors the original ``sidekick_bootstrap.apply_windows_utf8_bootstrap()``.
    """
    global _bootstrap_applied

    if not _IS_WINDOWS:
        return False
    if _bootstrap_applied:
        return False

    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass

    stdin = getattr(sys, "stdin", None)
    if stdin is not None:
        reconfigure = getattr(stdin, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

    _bootstrap_applied = True
    return True


# Apply on import — same as the original sidekick_bootstrap.
apply_windows_utf8_bootstrap()

__all__ = [
    "apply_windows_utf8_bootstrap",
]
