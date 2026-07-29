"""Durable policy decisions for Swarm action proposals."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import re
from typing import Any, Mapping

from .config import initialize_project
from .store import ProjectSwarmStore
from .types import (
    ActionCapabilities,
    ActionProposal,
    ApprovalRecord,
    SwarmRun,
    WorkflowRoleCheckpoint,
    thaw_json_value,
)
from .verifier import (
    InvalidVerifierResult,
    is_positive_verification_decision,
    verification_result_from_checkpoint_data,
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
    # These are workflow identities, not caller-provided approval labels.
    # A reviewed-execution quorum is intentionally locked to the two routed
    # independent review roles and their expected distinct model families.
    _REQUIRED_REVIEW_MODELS = {
        "review_a": ("glm-5.2", "glm-5"),
        "review_b": ("kimi-k2.7-code", "kimi-k2"),
    }
    _APPROVING_REVIEW_DECISIONS = frozenset({"approve", "approved"})
    _NOVA_PROPOSAL_ID = re.compile(r"^nova-([0-9a-f]{64})$")
    _MANAGED_OPERATION_FAMILIES = {
        "local.apply_patch": "target_local_worktree",
        "local.write_file": "target_local_worktree",
        "local.format": "target_local_worktree",
        "local.test": "target_local_worktree",
        "github.commit": "github_publication",
        "github.push": "github_publication",
        "github.pull_request": "github_publication",
        "github.release": "github_publication",
        "deployment.deploy": "target_deployment_worker",
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
        checkpoints = self.store.get_workflow_role_checkpoints(run.run_id)
        return self._evaluate_canonical(
            proposal,
            durable_run,
            approvals,
            checkpoints,
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
            checkpoints: Mapping[str, WorkflowRoleCheckpoint],
        ) -> tuple[PolicyDecision, bool]:
            decision = self._evaluate_canonical(
                proposal,
                durable_run,
                [
                    approval
                    for approval in approvals
                    if approval.proposal_digest == digest
                ],
                checkpoints,
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

    def authorize_managed_yolo_and_claim(
        self,
        proposal: ActionProposal,
        capability: object,
        *,
        worktree_identity: str,
        artifact_digest: str,
    ) -> PolicyDecision:
        """Claim one managed-Space action from durable, artifact-bound evidence.

        This is deliberately not a generic autonomy override. Only the
        supervisor's opaque capability can select this path, and all remaining
        authority is reconstructed under the store's atomic claim transaction.
        """
        from nova.space_supervisor import ManagedSpaceCapability

        if not isinstance(capability, ManagedSpaceCapability):
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "managed_capability_required",
            )
        canonical_root = capability._canonical_root
        run_id = capability._run_id
        family = self._MANAGED_OPERATION_FAMILIES.get(
            proposal.requested_action.name
        )
        if (
            family is None
            or family not in capability._allowed_action_families
            or proposal.category != "managed"
            or proposal.requested_action.workspace != canonical_root
            or not proposal.requested_action.use_worktree
            or self.store.project_root != canonical_root
        ):
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "managed_policy_scope_mismatch",
            )
        if (
            not isinstance(worktree_identity, str)
            or not worktree_identity.strip()
            or not isinstance(artifact_digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", artifact_digest) is None
        ):
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "managed_evidence_binding_invalid",
            )
        digest = proposal_digest(proposal)

        def authorize(
            durable_run: SwarmRun | None,
            approvals: list[ApprovalRecord],
            checkpoints: Mapping[str, WorkflowRoleCheckpoint],
        ) -> tuple[PolicyDecision, bool]:
            decision = self._evaluate_managed_yolo(
                proposal,
                durable_run,
                [
                    approval
                    for approval in approvals
                    if approval.proposal_digest == digest
                ],
                checkpoints,
                run_id=run_id,
                worktree_identity=worktree_identity,
                artifact_digest=artifact_digest,
                proposal_digest_value=digest,
            )
            return decision, decision.status is PolicyStatus.ALLOWED

        decision, claimed = self.store.authorize_and_claim(
            run_id,
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

    def _evaluate_managed_yolo(
        self,
        proposal: ActionProposal,
        durable_run: SwarmRun | None,
        approvals: list[ApprovalRecord],
        checkpoints: Mapping[str, WorkflowRoleCheckpoint],
        *,
        run_id: str,
        worktree_identity: str,
        artifact_digest: str,
        proposal_digest_value: str,
    ) -> PolicyDecision:
        if durable_run is None or durable_run.run_id != run_id:
            return self._decision(proposal, PolicyStatus.BLOCKED, "unknown_run")
        if durable_run.status != "running":
            return self._decision(proposal, PolicyStatus.BLOCKED, "run_not_running")
        if any(not approval.approved for approval in approvals):
            return self._decision(proposal, PolicyStatus.BLOCKED, "approval_denied")

        verifier = checkpoints.get("verifier")
        if verifier is None or verifier.model is not None:
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "managed_verifier_required",
            )
        try:
            result = verification_result_from_checkpoint_data(verifier.data)
        except InvalidVerifierResult:
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "managed_verifier_invalid",
            )
        if not is_positive_verification_decision(result.decision):
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "managed_verifier_not_positive",
            )
        if not (set(result.evidence) & set(proposal.evidence_refs)):
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "managed_verifier_evidence_mismatch",
            )
        test_evidence = result.test_evidence
        if test_evidence is None:
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "test_evidence_required",
            )
        if test_evidence.passed is not True:
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "test_evidence_not_positive",
            )
        if (
            test_evidence.run_id != run_id
            or test_evidence.worktree_identity != worktree_identity
            or test_evidence.artifact_digest != artifact_digest
            or test_evidence.report_ref not in proposal.evidence_refs
            or proposal.requested_action.arguments.get("artifact_digest")
            != artifact_digest
        ):
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "test_evidence_mismatch",
            )

        review_bindings = {
            "run_id": run_id,
            "worktree_identity": worktree_identity,
            "artifact_digest": artifact_digest,
            "proposal_digest": proposal_digest_value,
        }
        for role, (expected_model, _family) in self._REQUIRED_REVIEW_MODELS.items():
            checkpoint = checkpoints.get(role)
            if (
                checkpoint is None
                or checkpoint.model != expected_model
                or not self._valid_checkpoint_evidence(checkpoint)
                or not self._has_positive_review_vote(checkpoint)
                or test_evidence.report_ref
                not in self._valid_checkpoint_evidence(checkpoint)
                or any(
                    checkpoint.data.get(field) != expected
                    for field, expected in review_bindings.items()
                )
            ):
                return self._decision(
                    proposal,
                    PolicyStatus.NEEDS_MODEL_QUORUM,
                    "managed_review_quorum_required",
                )
        return self._decision(
            proposal,
            PolicyStatus.ALLOWED,
            "managed_policy_satisfied",
        )

    def _evaluate_canonical(
        self,
        proposal: ActionProposal,
        durable_run: SwarmRun | None,
        approvals: list[ApprovalRecord],
        checkpoints: Mapping[str, WorkflowRoleCheckpoint],
        capabilities: ActionCapabilities,
    ) -> PolicyDecision:
        if durable_run is None:
            return self._decision(proposal, PolicyStatus.BLOCKED, "unknown_run")
        if proposal.requested_action.workspace != self.store.project_root:
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "proposal_workspace_outside_project",
            )
        if capabilities != proposal.declared_capabilities():
            return self._decision(
                proposal,
                PolicyStatus.BLOCKED,
                "untrusted_action_capabilities_mismatch",
            )
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

        if not self._has_durable_review_quorum(proposal, checkpoints):
            return self._decision(
                proposal,
                PolicyStatus.NEEDS_MODEL_QUORUM,
                "verifier_and_independent_model_quorum_required",
            )
        return self._decision(proposal, PolicyStatus.ALLOWED, "policy_satisfied")

    @classmethod
    def _has_durable_review_quorum(
        cls,
        proposal: ActionProposal,
        checkpoints: Mapping[str, WorkflowRoleCheckpoint],
    ) -> bool:
        """Verify run-local verifier and independent review outcomes directly.

        Generic approval rows are intentionally not inputs here: their role,
        approver and model-family labels can be chosen by a caller.  Checkpoint
        rows are created only by the workflow's atomic completion path and are
        read under the same SQLite transaction as an execution claim.
        """
        verifier = checkpoints.get("verifier")
        if verifier is None or verifier.model is not None:
            return False
        verifier_evidence = cls._verified_local_verifier_evidence(verifier)
        if not verifier_evidence or not (
            verifier_evidence & set(proposal.evidence_refs)
        ):
            return False

        observed_families: set[str] = set()
        required_bindings = cls._nova_review_bindings(proposal)
        if (
            proposal.requested_action.name.startswith("nova:")
            and required_bindings is None
        ):
            return False
        for role, (expected_model, expected_family) in cls._REQUIRED_REVIEW_MODELS.items():
            checkpoint = checkpoints.get(role)
            if (
                checkpoint is None
                or checkpoint.model != expected_model
                or not cls._valid_checkpoint_evidence(checkpoint)
                or not cls._has_positive_review_vote(checkpoint)
                or (
                    required_bindings is not None
                    and any(
                        checkpoint.data.get(field) != value
                        for field, value in required_bindings.items()
                    )
                )
            ):
                return False
            # The model name is an exact workflow route; the paired family is
            # fixed policy metadata rather than an approval payload supplied
            # by a browser, model, or adapter.
            observed_families.add(expected_family)
        return len(observed_families) == len(cls._REQUIRED_REVIEW_MODELS)

    @classmethod
    def _nova_review_bindings(
        cls,
        proposal: ActionProposal,
    ) -> Mapping[str, str] | None:
        """Derive host-checkable bindings for a canonical Nova proposal."""
        if not proposal.requested_action.name.startswith("nova:"):
            return None
        match = cls._NOVA_PROPOSAL_ID.fullmatch(proposal.proposal_id)
        if match is None:
            return None
        intent_digest = match.group(1)
        intent = proposal.requested_action.arguments.get("intent")
        action = proposal.requested_action.name.removeprefix("nova:")
        if (
            not isinstance(intent, Mapping)
            or intent.get("id") != proposal.proposal_id
            or intent.get("action") != action
            or f"nova:verifier:{intent_digest}" not in proposal.evidence_refs
        ):
            return None
        return {
            "intent_digest": intent_digest,
            "proposal_digest": proposal_digest(proposal),
        }

    @staticmethod
    def _verified_local_verifier_evidence(
        checkpoint: WorkflowRoleCheckpoint,
    ) -> set[str]:
        """Return evidence only from an available, local verifier result.

        The default verifier intentionally records an auditable unavailable
        state when no read-only inspection adapter is configured.  Its marker
        is useful for diagnostics, but it must never unlock a project write.
        Reconstructing the typed result also requires local read-only
        provenance and verifier-owned assessment bindings rather than trusting
        a hand-assembled generic checkpoint dictionary.
        """
        try:
            result = verification_result_from_checkpoint_data(checkpoint.data)
        except InvalidVerifierResult:
            return set()
        if not is_positive_verification_decision(result.decision):
            return set()
        return set(result.evidence)

    @classmethod
    def _has_positive_review_vote(cls, checkpoint: WorkflowRoleCheckpoint) -> bool:
        """Accept only an explicit, unambiguous positive review verdict.

        The checkpoint's evidence and decision fields are useful audit data,
        but merely having a non-empty decision is not a vote to execute.  The
        producer must persist both a boolean approval and one of the canonical
        positive decision values; negative, ambiguous, legacy free-text, and
        type-confused values fail closed.
        """
        data = checkpoint.data
        decision = data.get("decision")
        return (
            data.get("approved") is True
            and isinstance(decision, str)
            and decision.strip().lower() in cls._APPROVING_REVIEW_DECISIONS
        )

    @staticmethod
    def _valid_checkpoint_evidence(
        checkpoint: WorkflowRoleCheckpoint,
    ) -> set[str]:
        """Return evidence refs only from a complete, string-addressable result."""
        data = checkpoint.data
        work = data.get("work")
        decision = data.get("decision")
        evidence = data.get("evidence")
        if (
            not isinstance(work, str)
            or not work.strip()
            or not isinstance(decision, str)
            or not decision.strip()
            or not isinstance(evidence, list)
            or not evidence
            or any(not isinstance(ref, str) or not ref.strip() for ref in evidence)
        ):
            return set()
        return {ref.strip() for ref in evidence}

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
