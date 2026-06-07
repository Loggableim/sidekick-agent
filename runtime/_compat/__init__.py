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
# ``from run_agent import AIAgent`` resolves without an import hook.
# ---------------------------------------------------------------------------
import runtime._compat.run_agent as _run_agent_stub  # noqa: E402

sys.modules["run_agent"] = _run_agent_stub

# ---------------------------------------------------------------------------
# Register runtime._compat.shim_state as sidekick_state so that code
# importing from sidekick_state resolves without an import hook.
# ---------------------------------------------------------------------------
import runtime._compat.shim_state as _shim_state  # noqa: E402

sys.modules["sidekick_state"] = _shim_state
