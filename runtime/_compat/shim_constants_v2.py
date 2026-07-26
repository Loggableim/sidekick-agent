"""Compatibility import path for Sidekick runtime constants.

New code imports :mod:`runtime._compat.shim_constants` directly.  This module
remains only so packaged runtime modules that still reference the v2 path use
the same Sidekick-only implementation.
"""

from runtime._compat.shim_constants import *  # noqa: F401,F403
from runtime._compat.shim_constants import __all__
