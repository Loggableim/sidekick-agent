"""Forwarder stub."""
from __future__ import annotations
from runtime.gateway.platforms.base import BasePlatformAdapter
import logging
logger = logging.getLogger(__name__)
def check_slack_requirements() -> bool:
    try:
        import slack_bolt  # noqa: F401
        return True
    except ImportError:
        return False
class SlackAdapter(BasePlatformAdapter):
    pass
