"""WebUI intent helpers for persistent Kanban orchestration."""

from __future__ import annotations

import os
import re
import threading
from typing import Any


_ORCHESTRATION_VERB = (
    r"(?:orchestriert|orchestriere|orchestrieren|orchestrier|"
    r"orchestrate|orchestrated|orchestrating)"
)
_KANBAN_ORCHESTRATION_LOCAL = threading.local()


def set_webui_kanban_orchestration(enabled: bool) -> None:
    """Set the WebUI orchestration marker for the current agent thread."""
    _KANBAN_ORCHESTRATION_LOCAL.enabled = bool(enabled)


def clear_webui_kanban_orchestration() -> None:
    """Clear the current thread's WebUI orchestration marker."""
    try:
        del _KANBAN_ORCHESTRATION_LOCAL.enabled
    except AttributeError:
        pass


def is_webui_kanban_orchestrated() -> bool:
    """Return the thread-safe WebUI marker, with an env fallback for workers/tests."""
    if hasattr(_KANBAN_ORCHESTRATION_LOCAL, "enabled"):
        return bool(_KANBAN_ORCHESTRATION_LOCAL.enabled)
    return bool(os.environ.get("SIDEKICK_KANBAN_ORCHESTRATED"))


def is_kanban_orchestration_request(message: str | None) -> bool:
    """Return whether *message* explicitly asks to orchestrate via Kanban."""
    text = " ".join(str(message or "").strip().lower().split())
    if not text or "kanban" not in text:
        return False

    kanban = r"\bkanban(?:[-\s]+board)?\b"
    verb = rf"\b{_ORCHESTRATION_VERB}\b"
    return bool(
        re.search(rf"{kanban}\W+{verb}", text)
        or re.search(rf"{verb}(?:\W+\w+){{0,8}}\W+{kanban}", text)
    )


def _toolset_list(value: Any, default_toolsets: list[str] | None) -> list[str]:
    if value is None:
        value = default_toolsets or []
    if isinstance(value, str):
        value = [value]
    return list(value or [])


def session_has_kanban_orchestration(session: Any) -> bool:
    """Return whether a session has persisted the Kanban toolset opt-in."""
    return "kanban" in _toolset_list(getattr(session, "enabled_toolsets", None), None)


def webui_toolsets_for_session(
    toolsets: list[str],
    session: Any,
    *,
    profile_has_kanban: bool,
) -> list[str]:
    """Filter inferred WebUI toolsets without changing profile configuration."""
    resolved = list(toolsets or [])
    if profile_has_kanban or session_has_kanban_orchestration(session):
        return resolved
    return [toolset for toolset in resolved if toolset != "kanban"]


def activate_kanban_orchestration(
    session: Any,
    message: str | None,
    default_toolsets: list[str] | None = None,
) -> bool:
    """Enable Kanban on *session* when the message contains the trigger."""
    if not is_kanban_orchestration_request(message):
        return False

    toolsets = _toolset_list(
        getattr(session, "enabled_toolsets", None),
        default_toolsets,
    )
    if "kanban" not in toolsets:
        toolsets.append("kanban")
    session.enabled_toolsets = toolsets
    return True
