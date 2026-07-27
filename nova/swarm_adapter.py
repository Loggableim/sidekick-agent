"""Prepared, opt-in bridge from Nova suggestions to the Swarm policy boundary.

This module deliberately has no startup hook.  Constructing an adapter is
inert, and execution is disabled unless a caller explicitly passes
``enabled=True`` after a separate runtime rollout.  It does not know a live
Nova deployment path and accepts an already-owned kernel instance instead.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from swarm_core.policy import PolicyDecision, PolicyGate, PolicyStatus
from swarm_core.types import (
    ActionCapabilities,
    ActionProposal,
    RequestedToolAction,
    SwarmRun,
)


class EntityKernelProtocol(Protocol):
    """The only Nova runtime surface this adapter is allowed to invoke."""

    def govern(self, proposal: dict[str, Any]) -> dict[str, Any]: ...

    def act(self, decision: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class NovaActionSpec:
    """Adapter-owned risk and Nova-governance classification for one action."""

    action: str
    capabilities: ActionCapabilities
    policy_tier: str


@dataclass(frozen=True)
class NovaSwarmExecution:
    """An auditable result with no implicit fallback action path."""

    proposal: ActionProposal
    swarm_decision: PolicyDecision | None
    nova_decision: Mapping[str, Any] | None
    executed: bool
    reason: str | None
    result: Any | None = None


_LOCAL_REVERSIBLE = ActionCapabilities(
    category="project",
    reversible=True,
    external=False,
    cost_increasing=False,
)

# This small initial registry is intentionally conservative.  Adding an action
# requires an explicit review of both its Swarm capabilities and Nova policy
# tier; arbitrary suggestions cannot label themselves safe.
NOVA_ACTION_SPECS: Mapping[str, NovaActionSpec] = MappingProxyType(
    {
        "agenda_update": NovaActionSpec("agenda_update", _LOCAL_REVERSIBLE, "silent"),
        "blog_draft": NovaActionSpec("blog_draft", _LOCAL_REVERSIBLE, "internal"),
        "inner_voice": NovaActionSpec("inner_voice", _LOCAL_REVERSIBLE, "internal"),
        "mind_diary": NovaActionSpec("mind_diary", _LOCAL_REVERSIBLE, "internal"),
        "prioritize_thread": NovaActionSpec(
            "prioritize_thread", _LOCAL_REVERSIBLE, "silent"
        ),
        "reflection": NovaActionSpec("reflection", _LOCAL_REVERSIBLE, "internal"),
    }
)


class NovaSwarmAdapter:
    """Translate vetted Nova suggestions and execute only through both gates."""

    def __init__(
        self,
        kernel: EntityKernelProtocol,
        policy_gate: PolicyGate,
        *,
        enabled: bool = False,
    ) -> None:
        if not isinstance(enabled, bool):
            raise TypeError("enabled must be a bool")
        self._kernel = kernel
        self._policy_gate = policy_gate
        self.enabled = enabled

    def translate(self, suggestion: Mapping[str, Any]) -> ActionProposal:
        """Return the exact, immutable Swarm proposal for a Nova suggestion."""
        proposal, _intent, _spec = self._prepare(suggestion)
        return proposal

    def execute_suggestion(
        self,
        suggestion: Mapping[str, Any],
        run: SwarmRun,
    ) -> NovaSwarmExecution:
        """Pass an explicit suggestion through Swarm, then Nova governance.

        No method here dispatches a legacy Nova action directly: a disabled
        adapter, Swarm policy result, malformed Nova governance result or Nova
        policy denial all return without calling ``EntityKernel.act``.
        """
        proposal, intent, spec = self._prepare(suggestion)
        if not self.enabled:
            return NovaSwarmExecution(
                proposal=proposal,
                swarm_decision=None,
                nova_decision=None,
                executed=False,
                reason="adapter_disabled",
            )

        swarm_decision = self._policy_gate.authorize_and_claim(
            proposal,
            run,
            spec.capabilities,
        )
        if swarm_decision.status is not PolicyStatus.ALLOWED:
            return NovaSwarmExecution(
                proposal=proposal,
                swarm_decision=swarm_decision,
                nova_decision=None,
                executed=False,
                reason=swarm_decision.reason,
            )

        nova_decision = self._kernel.govern(intent)
        policy = (
            nova_decision.get("policy") if isinstance(nova_decision, Mapping) else None
        )
        if not isinstance(policy, Mapping) or policy.get("allowed") is not True:
            reason = (
                str(policy.get("reason") or "nova_policy_blocked")
                if isinstance(policy, Mapping)
                else "nova_governance_malformed"
            )
            return NovaSwarmExecution(
                proposal=proposal,
                swarm_decision=swarm_decision,
                nova_decision=nova_decision
                if isinstance(nova_decision, Mapping)
                else None,
                executed=False,
                reason=reason,
            )

        result = self._kernel.act(dict(nova_decision))
        return NovaSwarmExecution(
            proposal=proposal,
            swarm_decision=swarm_decision,
            nova_decision=nova_decision,
            executed=bool(result.get("executed"))
            if isinstance(result, Mapping)
            else False,
            reason=None,
            result=result,
        )

    def _prepare(
        self,
        suggestion: Mapping[str, Any],
    ) -> tuple[ActionProposal, dict[str, Any], NovaActionSpec]:
        if not isinstance(suggestion, Mapping):
            raise TypeError("Nova suggestion must be a mapping")
        proposal_id = _required_text(
            suggestion.get("proposal_id")
            or suggestion.get("intent_id")
            or suggestion.get("id"),
            "Nova suggestion id",
        )
        action = _required_text(suggestion.get("action"), "Nova action")
        try:
            spec = NOVA_ACTION_SPECS[action]
        except KeyError as exc:
            raise ValueError(f"unsupported Nova action: {action}") from exc

        target = _mapping_field(suggestion, "target")
        payload = _mapping_field(suggestion, "payload")
        expected_outcome = _mapping_field(suggestion, "expected_outcome")
        evidence_refs = _evidence_refs(suggestion.get("evidence_refs", ()))
        intent = {
            "id": proposal_id,
            "intent_id": proposal_id,
            "need": _optional_text(suggestion.get("need"), "autonomy"),
            "action": spec.action,
            "title": _optional_text(suggestion.get("title"), spec.action),
            "why": _optional_text(
                suggestion.get("why"), "Nova proposed this Swarm-governed action."
            ),
            "target": target,
            "payload": payload,
            "expected_outcome": expected_outcome,
            "evidence_refs": list(evidence_refs),
            "priority": _priority(suggestion.get("priority", 0.5)),
            "policy_tier": spec.policy_tier,
            "tier": spec.policy_tier,
            "source": "nova_swarm_adapter",
        }
        workspace = _policy_project_root(self._policy_gate)
        requested_action = RequestedToolAction(
            name=f"nova:{spec.action}",
            workspace=workspace,
            arguments={"intent": intent},
            use_worktree=False,
        )
        proposal = ActionProposal(
            proposal_id=proposal_id,
            category=spec.capabilities.category,
            reversible=spec.capabilities.reversible,
            external=spec.capabilities.external,
            cost_increasing=spec.capabilities.cost_increasing,
            evidence_refs=evidence_refs,
            requested_action=requested_action,
        )
        return proposal, intent, spec


def _policy_project_root(policy_gate: PolicyGate) -> Path:
    store = getattr(policy_gate, "store", None)
    project_root = getattr(store, "project_root", None)
    if project_root is None:
        raise TypeError("NovaSwarmAdapter requires a project-bound PolicyGate")
    return Path(project_root).resolve()


def _required_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, default: str) -> str:
    if value is None:
        return default
    return _required_text(value, "Nova suggestion text")


def _mapping_field(suggestion: Mapping[str, Any], name: str) -> dict[str, Any]:
    value = suggestion.get(name, {})
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise TypeError(f"Nova suggestion {name} must be an object")
    return dict(value)


def _evidence_refs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise TypeError("Nova suggestion evidence_refs must be a list")
    refs = tuple(value)
    if any(
        not isinstance(reference, str) or not reference.strip() for reference in refs
    ):
        raise ValueError("Nova suggestion evidence_refs must contain non-empty strings")
    return tuple(reference.strip() for reference in refs)


def _priority(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("Nova suggestion priority must be a finite number")
    priority = float(value)
    if not math.isfinite(priority):
        raise ValueError("Nova suggestion priority must be a finite number")
    return max(0.0, min(1.0, priority))
