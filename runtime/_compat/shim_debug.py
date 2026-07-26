"""Shim for sidekick_cli.debug — provides paste sweep function."""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _sweep_expired_pastes(sidekick_home: str | None = None) -> None:
    """Remove expired pastes. Stub — paste functionality not yet ported."""
    logger.debug("paste sweep called (stub — no-op)")
