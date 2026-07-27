"""Durable policy decisions for Swarm action proposals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from .config import initialize_project
from .store import ProjectSwarmStore
from .types import (
    ActionCapabilities,
    ActionProposal,
    ApprovalRecord,
    SwarmRun,
    thaw_json_value,
)


class PolicyStatus(str, Enum):
    ALLOWED = "allowed"
    NEEDS_MODEL_QUORUM = "needs_model_quorum"
    NEEDS_HUMAN_APPROVAL = "needs_human_approval"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class PolicyDecision:
    proposal_id: str
    status: PolicyStatus
    reason: str


class PolicyGate:
    """Classify proposals using one project's durable approval history."""

    _LOCAL_REVERSIBLE_CATEGORIES = {"project", "git", "worktree"}
    _AUTONOMY_LEVELS = {
        "observe",
        "suggest",
        "execute_safe",
        "reviewed_execution",
        "autonomous",
    }

    def __init__(
        self,
        store: ProjectSwarmStore,
        *,
        default_autonomy: str | None = None,
    ) -> None:
        if default_autonomy is None:
            default_autonomy = initialize_project(store.project_root).default_autonomy
        if default_autonomy not in self._AUTONOMY_LEVELS:
            raise ValueError(f"Unsupported Swarm autonomy level: {default_autonomy}")
        self.store = store
        self.default_autonomy = default_autonomy

    def evaluate(
        self,
        proposal: ActionProposal,
        run: SwarmRun,
        capabilities: ActionCapabilities | None = None,
    ) -> PolicyDecision:
        durable_run = self.store.get_run(run.run_id)
        approvals = self._matching_approvals(proposal, run)
        return self._evaluate_canonical(
            proposal,
            durable_run,
            approvals,
            capabilities or proposal.declared_capabilities(),
        )

    def authorize_and_claim(
        self,
        proposal: ActionProposal,
        run: SwarmRun,
        capabilities: ActionCapabilities,
    ) -> PolicyDecision:
        """Atomically re-evaluate canonical policy state and claim one execution."""
        digest = proposal_digest(proposal)

        def authorize(
            durable_run: SwarmRun | None,
            approvals: list[ApprovalRecord],
        ) -> tuple[PolicyDecision, bool]:
            decision = self._evaluate_canonical(
                proposal,
                durable_run,
                [
                    approval
                    for approval in approvals
                    if approval.proposal_digest == digest
                ],
                capabilities,
            )
            return decision, decision.status is PolicyStatus.ALLOWED

        decision, claimed = self.store.authorize_and_claim(
            run.run_id,
            proposal.proposal_id,
            digest,
            authorize,
        )
        if decision.status is not PolicyStatus.ALLOWED:
            return decision
        if not claimed:
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "execution_already_claimed",
            )
        return decision

    def _evaluate_canonical(
        self,
        proposal: ActionProposal,
        durable_run: SwarmRun | None,
        approvals: list[ApprovalRecord],
        capabilities: ActionCapabilities,
    ) -> PolicyDecision:
        if capabilities != proposal.declared_capabilities():
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "untrusted_action_capabilities_mismatch",
            )
        if durable_run is None:
            return self._decision(proposal, PolicyStatus.BLOCKED, "unknown_run")
        if durable_run.status != "running":
            return self._decision(proposal, PolicyStatus.BLOCKED, "run_not_running")

        autonomy = str(durable_run.metadata.get("autonomy", self.default_autonomy))
        if autonomy not in self._AUTONOMY_LEVELS:
            return self._decision(
                proposal, PolicyStatus.BLOCKED, "unsupported_autonomy"
            )
        if autonomy == "observe":
            return self._decision(proposal, PolicyStatus.BLOCKED, "observe_only")

        if any(not approval.approved for approval in approvals):
            return self._decision(proposal, PolicyStatus.BLOCKED, "approval_denied")

        human_approved = any(
            approval.approval_type == "human" and approval.approved
            for approval in approvals
        )
        sensitive = (
            capabilities.external
            or not capabilities.reversible
            or capabilities.cost_increasing
        )
        if sensitive and not human_approved:
            return self._decision(
                proposal,
                PolicyStatus.NEEDS_HUMAN_APPROVAL,
                "sensitive_action_requires_human",
            )
        if sensitive:
            return self._decision(
                proposal, PolicyStatus.ALLOWED, "human_approval_recorded"
            )
        if capabilities.category not in self._LOCAL_REVERSIBLE_CATEGORIES:
            return self._decision(
                proposal, PolicyStatus.BLOCKED, "unclassified_action_category"
            )
        if autonomy == "suggest" and not human_approved:
            return self._decision(
                proposal,
                PolicyStatus.NEEDS_HUMAN_APPROVAL,
                "suggest_mode_requires_human",
            )
        if autonomy in {"suggest", "execute_safe", "autonomous"}:
            return self._decision(proposal, PolicyStatus.ALLOWED, "policy_satisfied")

        verified = any(
            approval.approval_type == "verifier"
            and approval.approved
            and bool(set(approval.evidence_refs) & set(proposal.evidence_refs))
            for approval in approvals
        )
        model_approvals = [
            approval
            for approval in approvals
            if approval.approval_type == "model"
            and approval.approved
            and approval.model_family
        ]
        families_by_approver: dict[str, set[str]] = {}
        for approval in model_approvals:
            families_by_approver.setdefault(approval.approver_id, set()).add(
                str(approval.model_family).strip().lower()
            )
        unambiguous_approvals = [
            (approver_id, next(iter(families)))
            for approver_id, families in families_by_approver.items()
            if len(families) == 1
        ]
        independent_pair = any(
            first_approver != second_approver and first_family != second_family
            for index, (first_approver, first_family) in enumerate(
                unambiguous_approvals
            )
            for second_approver, second_family in unambiguous_approvals[index + 1 :]
        )
        if not verified or not independent_pair:
            return self._decision(
                proposal,
                PolicyStatus.NEEDS_MODEL_QUORUM,
                "verifier_and_independent_model_quorum_required",
            )
        return self._decision(proposal, PolicyStatus.ALLOWED, "policy_satisfied")

    def record_approval(
        self,
        proposal: ActionProposal,
        run: SwarmRun,
        *,
        approval_type: str,
        approver_id: str,
        approved: bool = True,
        model_family: str | None = None,
        evidence_refs: tuple[str, ...] = (),
    ) -> ApprovalRecord:
        if approval_type not in {"verifier", "model", "human"}:
            raise ValueError(f"Unsupported approval type: {approval_type}")
        if approval_type == "model" and approved and not model_family:
            raise ValueError("A model approval requires its model family")
        normalized_family = str(model_family).strip().lower() if model_family else None
        return self.store.record_approval(
            run.run_id,
            proposal.proposal_id,
            proposal_digest(proposal),
            approval_type,
            approver_id,
            approved=approved,
            model_family=normalized_family,
            evidence_refs=evidence_refs,
        )

    def _matching_approvals(
        self,
        proposal: ActionProposal,
        run: SwarmRun,
    ) -> list[ApprovalRecord]:
        digest = proposal_digest(proposal)
        return [
            approval
            for approval in self.store.list_approvals(
                run.run_id, proposal_id=proposal.proposal_id
            )
            if approval.proposal_digest == digest
        ]

    @staticmethod
    def _decision(
        proposal: ActionProposal,
        status: PolicyStatus,
        reason: str,
    ) -> PolicyDecision:
        return PolicyDecision(proposal.proposal_id, status, reason)


def proposal_digest(proposal: ActionProposal) -> str:
    """Return a stable digest of every execution-relevant proposal field."""
    payload: dict[str, Any] = {
        "proposal_id": proposal.proposal_id,
        "category": proposal.category,
        "reversible": proposal.reversible,
        "external": proposal.external,
        "cost_increasing": proposal.cost_increasing,
        "evidence_refs": list(proposal.evidence_refs),
        "requested_action": {
            "name": proposal.requested_action.name,
            "workspace": str(proposal.requested_action.workspace),
            "arguments": thaw_json_value(proposal.requested_action.arguments),
            "use_worktree": proposal.requested_action.use_worktree,
        },
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
