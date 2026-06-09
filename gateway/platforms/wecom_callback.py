"""Forwarder stub."""
from __future__ import annotations
from runtime.gateway.platforms.base import BasePlatformAdapter
import logging
logger = logging.getLogger(__name__)
def check_wecom_callback_requirements() -> bool:
    return False
class WeComCallbackAdapter(BasePlatformAdapter):
    pass
