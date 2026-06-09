"""Compat shim package - bridges old import paths to the new sidekick structure.

Modules in this package re-export names from their canonical locations in
``shared.*`` so that legacy agent code using ``from runtime._compat.shim_constants import ...``
or ``from sidekick_logging import ...`` continues to work without changes.
"""

from __future__ import annotations

import sys

# ---------------------------------------------------------------------------
# Register runtime._compat.shim_time as sidekick_time so that code importing
# ``from sidekick_time import now`` resolves cleanly.
# ---------------------------------------------------------------------------
import runtime._compat.shim_time as _shim_time  # noqa: E402

sys.modules["sidekick_time"] = _shim_time

# ---------------------------------------------------------------------------
# Register run_agent stub so that cron and web code importing
# The real run_agent.py at the repo root is the canonical implementation.
# Do NOT register a stub alias here — it would shadow the real module
# and cause ``AIAgent.run_conversation(persist_user_message=...)`` to fail
# with a TypeError (the stub doesn't accept that parameter).

# ---------------------------------------------------------------------------
# Register runtime._compat.shim_state as sidekick_state so that code
# importing from sidekick_state resolves without an import hook.
# ---------------------------------------------------------------------------
import runtime._compat.shim_state as _shim_state  # noqa: E402

sys.modules["sidekick_state"] = _shim_state
