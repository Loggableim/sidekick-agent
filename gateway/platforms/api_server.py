"""Forwarder stub."""
from __future__ import annotations
from runtime.gateway.platforms.base import BasePlatformAdapter
import logging
logger = logging.getLogger(__name__)
def check_api_server_requirements() -> bool:
    return True
class APIServerAdapter(BasePlatformAdapter):
    """API Server adapter — allows REST API clients to interact with the gateway."""
    
    def set_message_handler(self, h):
        self._msg_handler = h
    
    def set_fatal_error_handler(self, h):
        self._fatal_handler = h
    
    def set_session_store(self, s):
        self._session_store = s
    
    def set_busy_session_handler(self, h):
        self._busy_handler = h
    
    async def start(self):
        return True
    
    async def connect(self) -> bool:
        """Alias for start() — called by the gateway runner."""
        return await self.start()
    
    async def stop(self):
        pass
    
    async def send(self, chat_id: str, message: str, **kwargs) -> dict:
        return {"ok": True}
