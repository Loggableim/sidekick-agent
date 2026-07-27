"""Small event-publishing façade over the project-local store."""

from __future__ import annotations

from typing import Any, Mapping

from .store import ProjectSwarmStore
from .types import SwarmEvent


class SwarmEventBus:
    """Publish durable events without coupling callers to SQLite details."""

    def __init__(self, store: ProjectSwarmStore) -> None:
        self._store = store

    def publish(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        *,
        visibility: str = "project",
    ) -> SwarmEvent:
        return self._store.append_event(
            run_id, event_type, payload, visibility=visibility
        )
