"""Injected Sidekick action adapter with trusted-workspace enforcement."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping

from .types import (
    ActionCapabilities,
    ActionProposal,
    RequestedToolAction,
    thaw_json_value,
)

if TYPE_CHECKING:
    from nova.managed_space_gateway import (
        GatewayResult,
        ManagedSpaceActionGateway,
    )


TrustedWorkspaceResolver = Callable[[str | Path], Path]
ActionCallable = Callable[[str, Path, dict[str, Any]], Any]
WorktreeCreator = Callable[[Path], Mapping[str, Any] | str | Path]
WorktreeValidator = Callable[[Path, Path], bool]
ActionClassifier = Callable[[RequestedToolAction], ActionCapabilities]


class SidekickToolAdapter:
    """Bridge core requests to callables supplied by the Sidekick host."""

    def __init__(
        self,
        *,
        trusted_workspace_resolver: TrustedWorkspaceResolver,
        action_executor: ActionCallable,
        action_previewer: ActionCallable | None = None,
        worktree_creator: WorktreeCreator | None = None,
        worktree_validator: WorktreeValidator | None = None,
        action_classifier: ActionClassifier | None = None,
        managed_gateway: ManagedSpaceActionGateway | None = None,
    ) -> None:
        if managed_gateway is not None:
            from nova.managed_space_gateway import ManagedSpaceActionGateway

            if not isinstance(managed_gateway, ManagedSpaceActionGateway):
                raise TypeError(
                    "managed_gateway must be ManagedSpaceActionGateway"
                )
        self._resolve_trusted_workspace = trusted_workspace_resolver
        self._execute_action = action_executor
        self._preview_action = action_previewer
        self._create_worktree = worktree_creator
        self._validate_worktree = worktree_validator
        self._classify_action = action_classifier
        self._managed_gateway = managed_gateway

    def classify(self, action: RequestedToolAction) -> ActionCapabilities:
        if self._classify_action is None:
            return ActionCapabilities(
                category="unknown",
                reversible=False,
                external=True,
                cost_increasing=True,
            )
        capabilities = self._classify_action(action)
        if not isinstance(capabilities, ActionCapabilities):
            raise TypeError("Sidekick action classifier must return ActionCapabilities")
        return capabilities

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
        from nova.managed_space_gateway import ManagedSpaceActionGateway

        if ManagedSpaceActionGateway.handles(action.name):
            raise RuntimeError(
                "managed gateway handoff required for this operation"
            )
        workspace = self._execution_workspace(action)
        return self._execute_action(
            action.name,
            workspace,
            thaw_json_value(action.arguments),
        )

    def execute_managed(
        self,
        capability: object,
        proposal: ActionProposal,
    ) -> GatewayResult:
        """Route managed proposals only through the injected safety gateway."""
        if self._managed_gateway is None:
            raise RuntimeError("managed gateway is not configured")
        return self._managed_gateway.execute(capability, proposal)

    def _resolve_action_workspace(self, action: RequestedToolAction) -> Path:
        trusted = Path(self._resolve_trusted_workspace(action.workspace)).expanduser()
        if not trusted.is_absolute() or trusted != action.workspace:
            raise ValueError("Trusted workspace resolver must preserve action snapshot")
        return action.workspace

    def _execution_workspace(self, action: RequestedToolAction) -> Path:
        source = self._resolve_action_workspace(action)
        if not action.use_worktree:
            return source
        if self._create_worktree is None:
            raise RuntimeError("This Sidekick adapter has no worktree creator")
        created = self._create_worktree(source)
        if isinstance(created, Mapping):
            created_path = created.get("path")
        else:
            created_path = created
        if not created_path:
            raise RuntimeError("Sidekick worktree creator returned no path")
        created = Path(created_path).expanduser()
        if not created.is_absolute():
            raise ValueError("Sidekick worktree creator must return an absolute path")
        validated_created = Path(self._resolve_trusted_workspace(created)).expanduser()
        if not validated_created.is_absolute() or validated_created != created:
            raise ValueError(
                "Trusted workspace resolver must preserve worktree snapshot"
            )
        if self._validate_worktree is None or not self._validate_worktree(
            source, created
        ):
            raise ValueError("Created worktree is not bound to its approved source")
        return created
