"""Minimal AIAgent stub for the monorepo migration.

The real AIAgent (15,549 LOC in cids-hermes-agent/run_agent.py) will be
ported as a follow-up. This stub provides just enough to load the CLI
entrypoint and return useful error messages about missing runtime pieces.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Re-export _hermes_home from the real run_agent (lazy, to avoid circular imports)
_hermes_home = None
try:
    import run_agent as _real_run_agent
    _hermes_home = getattr(_real_run_agent, '_hermes_home', None)
except Exception:
    pass

# Re-export get_tool_definitions from the real run_agent
get_tool_definitions = None
try:
    import run_agent as _real_run_agent
    get_tool_definitions = getattr(_real_run_agent, 'get_tool_definitions', None)
except Exception:
    pass

# Try to load the real AIAgent if run_agent.py is available
_real_aiagent = None
try:
    import run_agent as _real_run_agent
    _real_aiagent = _real_run_agent.AIAgent
    logger.debug("Using real run_agent.AIAgent")
except Exception:
    logger.debug("Using stub AIAgent (real run_agent not importable)")


class AIAgent:
    """AIAgent — wraps real implementation or falls back to stub."""

    def __init__(self, **kwargs: Any) -> None:
        if _real_aiagent is not None:
            self._impl = _real_aiagent(**kwargs)
        else:
            self._impl = None
            self._kwargs = kwargs
            self._model = kwargs.get("model", "")
            self._provider = kwargs.get("provider", "")

    def chat(self, message: str) -> str:
        if self._impl is not None:
            return self._impl.chat(message)
        return f"[AIAgent stub] Provider={self._provider}, Model={self._model}. The full runtime has not been ported yet. Received: {message[:80]}..."

    def run_conversation(
        self,
        user_message: str,
        system_message: str | None = None,
        conversation_history: list | None = None,
        task_id: str | None = None,
        stream_callback: Optional[callable] = None,
        persist_user_message: Optional[str] = None,
    ) -> dict[str, Any]:
        if self._impl is not None:
            return self._impl.run_conversation(
                user_message=user_message,
                system_message=system_message,
                conversation_history=conversation_history,
                task_id=task_id,
                stream_callback=stream_callback,
                persist_user_message=persist_user_message,
            )
        return {
            "final_response": self.chat(user_message),
            "messages": [{"role": "user", "content": user_message}],
            "task_id": task_id,
        }


def _sanitize_surrogates(text: str) -> str:
    """Stub — removes surrogate characters."""
    return text.encode("utf-8", errors="replace").decode("utf-8")


__all__ = [
    "AIAgent",
    "_sanitize_surrogates",
    "_hermes_home",
    "get_tool_definitions",
]