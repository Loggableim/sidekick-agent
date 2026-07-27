"""Injected Sidekick action adapter with trusted-workspace enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .types import RequestedToolAction, thaw_json_value


TrustedWorkspaceResolver = Callable[[str | Path], Path]
ActionCallable = Callable[[str, Path, dict[str, Any]], Any]
WorktreeCreator = Callable[[Path], Mapping[str, Any] | str | Path]


class SidekickToolAdapter:
    """Bridge core requests to callables supplied by the Sidekick host."""

    def __init__(
        self,
        *,
        trusted_workspace_resolver: TrustedWorkspaceResolver,
        action_executor: ActionCallable,
        action_previewer: ActionCallable | None = None,
        worktree_creator: WorktreeCreator | None = None,
    ) -> None:
        self._resolve_trusted_workspace = trusted_workspace_resolver
        self._execute_action = action_executor
        self._preview_action = action_previewer
        self._create_worktree = worktree_creator

    def preview(self, action: RequestedToolAction) -> Any:
        workspace = self._resolve_action_workspace(action)
        arguments = thaw_json_value(action.arguments)
        if self._preview_action is not None:
            return self._preview_action(action.name, workspace, arguments)
        return {
            "action": action.name,
            "workspace": str(workspace),
            "arguments": arguments,
        }

    def execute(self, action: RequestedToolAction) -> Any:
        workspace = self._execution_workspace(action)
        return self._execute_action(
            action.name,
            workspace,
            thaw_json_value(action.arguments),
        )

    def _resolve_action_workspace(self, action: RequestedToolAction) -> Path:
        return (
            Path(self._resolve_trusted_workspace(action.workspace))
            .expanduser()
            .resolve()
        )

    def _execution_workspace(self, action: RequestedToolAction) -> Path:
        trusted = self._resolve_action_workspace(action)
        if not action.use_worktree:
            return trusted
        if self._create_worktree is None:
            raise RuntimeError("This Sidekick adapter has no worktree creator")
        created = self._create_worktree(trusted)
        if isinstance(created, Mapping):
            created_path = created.get("path")
        else:
            created_path = created
        if not created_path:
            raise RuntimeError("Sidekick worktree creator returned no path")
        return (
            Path(self._resolve_trusted_workspace(created_path)).expanduser().resolve()
        )
