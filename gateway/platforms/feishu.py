"""Forwarder stub."""
from __future__ import annotations
from runtime.gateway.platforms.base import BasePlatformAdapter
import logging
logger = logging.getLogger(__name__)
FEISHU_AVAILABLE = False
FEISHU_DOMAIN = "https://open.feishu.cn"
LARK_DOMAIN = "https://open.larksuite.com"
def check_feishu_requirements() -> bool:
    return False
class FeishuAdapter(BasePlatformAdapter):
    MAX_MESSAGE_LENGTH = 20000
