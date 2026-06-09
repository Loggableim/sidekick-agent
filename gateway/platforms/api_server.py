"""Forwarder stub."""
from __future__ import annotations
from runtime.gateway.platforms.base import BasePlatformAdapter
import logging
logger = logging.getLogger(__name__)
def check_api_server_requirements() -> bool:
    return True
class APIServerAdapter(BasePlatformAdapter):
    pass
