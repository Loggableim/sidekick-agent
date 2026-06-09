"""Forwarder stub — echte Adapter in runtime."""
from __future__ import annotations
from runtime.gateway.platforms.base import BasePlatformAdapter, MessageEvent
import logging
logger = logging.getLogger(__name__)
def check_discord_requirements() -> bool:
    try:
        import discord  # noqa: F401
        return True
    except ImportError:
        return False
class DiscordAdapter(BasePlatformAdapter):
    pass
