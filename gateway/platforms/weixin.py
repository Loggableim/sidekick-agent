"""Forwarder stub."""
from __future__ import annotations
from runtime.gateway.platforms.base import BasePlatformAdapter
import logging
logger = logging.getLogger(__name__)
def check_weixin_requirements() -> bool:
    return False
class WeixinAdapter(BasePlatformAdapter):
    pass
