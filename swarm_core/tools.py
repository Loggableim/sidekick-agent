"""Pluggable tool protocols and the policy-first execution boundary."""

from __future__ import annotations

from typing import Any, Protocol

from .policy import PolicyDecision, PolicyGate, PolicyStatus, proposal_digest
from .types import ActionProposal, RequestedToolAction, SwarmRun


class ToolAdapter(Protocol):
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
        decision = self.policy_gate.evaluate(proposal, run)
        if decision.status is not PolicyStatus.ALLOWED:
            raise ActionNotAllowed(decision)
        if not self.policy_gate.store.claim_execution(
            run.run_id,
            proposal.proposal_id,
            proposal_digest(proposal),
        ):
            raise ActionNotAllowed(
                PolicyDecision(
                    proposal.proposal_id,
                    PolicyStatus.BLOCKED,
                    "execution_already_claimed",
                )
            )
        return self.adapter.execute(proposal.requested_action)
