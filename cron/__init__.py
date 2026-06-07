"""Legacy cron package — forwards to runtime.cron.

This package exists so that ``from cron import get_job`` (used by cli/cli.py)
resolves correctly without the old ``cids-hermes-agent/cron/`` package.
"""
from __future__ import annotations

# Re-export everything from the canonical runtime.cron module
from runtime.cron.jobs import get_job  # noqa: F401
from runtime.cron.scheduler import *  # noqa: F401, F403

__all__: list[str] = []
