"""Minimal AIAgent stub for the monorepo migration.

The real AIAgent (15,549 LOC in cids-hermes-agent/run_agent.py) will be
ported as a follow-up. This stub provides just enough to load the CLI
entrypoint and return useful error messages about missing runtime pieces.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class AIAgent:
    """Minimal AIAgent stub — real implementation coming in next migration pass."""

    def __init__(self, **kwargs: Any) -> None:
        self._kwargs = kwargs
        self._model = kwargs.get("model", "")
        self._provider = kwargs.get("provider", "")

    def chat(self, message: str) -> str:
        """Simple chat interface — returns a stub response."""
        return f"[AIAgent stub] Provider={self._provider}, Model={self._model}. The full runtime has not been ported yet. Received: {message[:80]}..."

    def run_conversation(
        self, user_message: str, system_message: str | None = None,
        conversation_history: list | None = None, task_id: str | None = None,
    ) -> dict[str, Any]:
        """Full conversation interface."""
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
]