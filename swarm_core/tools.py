"""Pluggable tool protocols and the policy-first execution boundary."""

from __future__ import annotations

from typing import Any, Protocol

from .policy import PolicyDecision, PolicyGate, PolicyStatus
from .types import ActionCapabilities, ActionProposal, RequestedToolAction, SwarmRun


class ToolAdapter(Protocol):
    def classify(self, action: RequestedToolAction) -> ActionCapabilities: ...

    def preview(self, action: RequestedToolAction) -> Any: ...

    def execute(self, action: RequestedToolAction) -> Any: ...


class ActionNotAllowed(RuntimeError):
    def __init__(self, decision: PolicyDecision) -> None:
        super().__init__(
            f"Action {decision.proposal_id} is not allowed: {decision.status.value}"
        )
        self.decision = decision


class GatedToolExecutor:
    """Evaluate policy before crossing the injected adapter boundary."""

    def __init__(self, policy_gate: PolicyGate, adapter: ToolAdapter) -> None:
        self.policy_gate = policy_gate
        self.adapter = adapter

    def preview(self, proposal: ActionProposal) -> Any:
        return self.adapter.preview(proposal.requested_action)

    def execute(self, proposal: ActionProposal, run: SwarmRun) -> Any:
        decision = self.policy_gate.authorize_and_claim(
            proposal,
            run,
            self.adapter.classify(proposal.requested_action),
        )
        if decision.status is not PolicyStatus.ALLOWED:
            raise ActionNotAllowed(decision)
        return self.adapter.execute(proposal.requested_action)
