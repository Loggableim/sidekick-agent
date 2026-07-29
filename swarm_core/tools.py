"""Pluggable tool protocols and the policy-first execution boundary."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Protocol

from .policy import PolicyDecision, PolicyGate, PolicyStatus
from .types import ActionCapabilities, ActionProposal, RequestedToolAction, SwarmRun


class ToolAdapter(Protocol):
    def classify(self, action: RequestedToolAction) -> ActionCapabilities: ...

    def preview(self, action: RequestedToolAction) -> Any: ...

    def execute(self, action: RequestedToolAction) -> Any: ...


class HostBoundExecutionRoute:
    """Opaque host route for execution outside the generic adapter boundary."""

    __slots__ = ("_dispatch", "_project_root", "_run_id")

    def __init__(
        self,
        seal: object,
        *,
        project_root: Path,
        run_id: str,
        dispatch: Callable[[ActionProposal, SwarmRun], Any],
    ) -> None:
        if seal is not _HOST_ROUTE_SEAL:
            raise TypeError("Host-bound execution routes are created by trusted hosts")
        object.__setattr__(self, "_project_root", project_root)
        object.__setattr__(self, "_run_id", run_id)
        object.__setattr__(self, "_dispatch", dispatch)

    def __setattr__(self, name: str, value: object) -> None:
        del name, value
        raise AttributeError("HostBoundExecutionRoute is immutable")

    def __init_subclass__(cls, **kwargs: object) -> None:
        del kwargs
        raise TypeError("HostBoundExecutionRoute cannot be subclassed")


_HOST_ROUTE_SEAL = object()


def create_host_bound_execution_route(
    *,
    project_root: Path,
    run_id: str,
    dispatch: Callable[[ActionProposal, SwarmRun], Any],
) -> HostBoundExecutionRoute:
    canonical_root = Path(project_root).resolve()
    if canonical_root != Path(project_root) or not canonical_root.is_absolute():
        raise ValueError("Host-bound route requires a canonical project root")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("Host-bound route requires a run id")
    if not callable(dispatch):
        raise TypeError("Host-bound route dispatch must be callable")
    return HostBoundExecutionRoute(
        _HOST_ROUTE_SEAL,
        project_root=canonical_root,
        run_id=run_id,
        dispatch=dispatch,
    )


class ActionNotAllowed(RuntimeError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(
            f"Action {decision.proposal_id} is not allowed: {decision.status.value}"
        )
        self.decision = decision


class GatedToolExecutor:
    """Evaluate policy before crossing the injected adapter boundary."""

    def __init__(
        self,
        policy_gate: PolicyGate,
        adapter: ToolAdapter,
        *,
        host_bound_route: HostBoundExecutionRoute | None = None,
    ) -> None:
        if (
            host_bound_route is not None
            and type(host_bound_route) is not HostBoundExecutionRoute
        ):
            raise TypeError(
                "host_bound_route must be a trusted host-bound execution route"
            )
        self.policy_gate = policy_gate
        self.adapter = adapter
        self._host_bound_route = host_bound_route

    def preview(self, proposal: ActionProposal) -> Any:
        return self.adapter.preview(proposal.requested_action)

    def execute(self, proposal: ActionProposal, run: SwarmRun) -> Any:
        route = self._host_bound_route
        if route is not None:
            durable_run = self.policy_gate.store.get_run(run.run_id)
            if (
                durable_run is not None
                and route._project_root == self.policy_gate.store.project_root
                and route._run_id == durable_run.run_id
            ):
                return route._dispatch(proposal, durable_run)
        decision = self.policy_gate.authorize_and_claim(
            proposal,
            run,
            self.adapter.classify(proposal.requested_action),
        )
        if decision.status is not PolicyStatus.ALLOWED:
            raise ActionNotAllowed(decision)
        return self.adapter.execute(proposal.requested_action)
