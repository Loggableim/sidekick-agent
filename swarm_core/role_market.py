"""Transparent, local model-candidate evaluation without route authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .learning import ReputationLedger
from .models import (
    MODEL_HEALTH_HEALTHY,
    OLLAMA_CLOUD_PROVIDER,
    ModelRegistry,
    ModelSpec,
)
from .router import ModelRouter, NoEligibleModel, _ROLE_CHAINS


# The market may explain these routes, but it never supplies execution order.
# ``ModelRouter`` remains the sole authority for the prescribed chains.
SAFETY_LOCKED_ROLES = frozenset({*_ROLE_CHAINS, "vision"})
_STATIC_QUALITY_WEIGHT = 0.8
_LOCAL_REPUTATION_WEIGHT = 0.2
# These workflow subroles intentionally inherit the curated planning basis.
# The mapping is exposed on every assessment, rather than silently pretending
# they have independent ModelSpec quality data.
_ROLE_QUALITY_BASES = {
    "planner_challenger": "planner",
    "planner_arbitrator": "planner",
}


@dataclass(frozen=True)
class RoleMarketCandidate:
    """One curated candidate and every input used to assess it."""

    model: str
    family: str
    provider: str
    health: str
    role_quality: float
    reputation: float | None
    score: float
    eligible: bool
    ineligibility_reasons: tuple[str, ...]


@dataclass(frozen=True)
class RoleMarketAssessment:
    """Read-only market view for one role/capability decision."""

    role: str
    capability: str
    quality_basis: str
    reputation_basis: str
    requirements: frozenset[str]
    safety_locked: bool
    prescribed_models: tuple[str, ...]
    recommended_models: tuple[str, ...]
    candidates: tuple[RoleMarketCandidate, ...]


class RoleMarket:
    """Score catalog-backed role candidates without selecting a route.

    Static role quality is deliberately the dominant term.  Project-local
    reputation is a bounded confidence signal for the role/capability pair;
    it is never discovered from a model response and never changes a router
    chain.  A missing reputation remains ``None`` rather than being invented.
    """

    def __init__(self, registry: ModelRegistry, ledger: ReputationLedger) -> None:
        if not isinstance(registry, ModelRegistry):
            raise TypeError("RoleMarket requires a ModelRegistry")
        if not isinstance(ledger, ReputationLedger):
            raise TypeError("RoleMarket requires a ReputationLedger")
        self.registry = registry
        self.ledger = ledger
        self._router = ModelRouter(registry)

    def assess(
        self,
        role: str,
        capability: str,
        *,
        requirements: Iterable[str] = (),
    ) -> RoleMarketAssessment:
        """Return all curated candidates and a non-authoritative ranking."""
        normalized_role = _require_text(role, "Role")
        normalized_capability = _require_text(capability, "Capability")
        normalized_requirements = frozenset(
            _require_text(requirement, "Requirement").lower()
            for requirement in requirements
        )
        quality_basis = _ROLE_QUALITY_BASES.get(normalized_role, normalized_role)
        # Planning subroles use the same verified planning reputation as their
        # static quality basis.  The explicit fields below keep that inherited
        # signal inspectable for callers and UIs.
        reputation_basis = quality_basis
        reputation = self.ledger.score(reputation_basis, normalized_capability)
        configured_models = _configured_safety_models(normalized_role)
        candidates = tuple(
            sorted(
                (
                    self._candidate(
                        spec,
                        quality_basis=quality_basis,
                        requirements=normalized_requirements,
                        reputation=reputation,
                    )
                    for spec in self.registry.all_models()
                    if (
                        spec.name in configured_models
                        if configured_models
                        else spec.quality_for(quality_basis) is not None
                    )
                ),
                key=lambda candidate: (
                    not candidate.eligible,
                    -candidate.score,
                    -candidate.role_quality,
                    candidate.model,
                ),
            )
        )
        safety_locked = normalized_role in SAFETY_LOCKED_ROLES
        prescribed_models = self._prescribed_models(
            normalized_role,
            normalized_requirements,
        )
        return RoleMarketAssessment(
            role=normalized_role,
            capability=normalized_capability,
            quality_basis=quality_basis,
            reputation_basis=reputation_basis,
            requirements=normalized_requirements,
            safety_locked=safety_locked,
            prescribed_models=prescribed_models,
            recommended_models=tuple(
                candidate.model for candidate in candidates if candidate.eligible
            ),
            candidates=candidates,
        )

    def _candidate(
        self,
        spec: ModelSpec,
        *,
        quality_basis: str,
        requirements: frozenset[str],
        reputation: float | None,
    ) -> RoleMarketCandidate:
        role_quality = spec.quality_for(quality_basis)
        if role_quality is None:
            # A safety route can only list a model with a declared basis.
            raise ValueError(
                f"Model {spec.name!r} has no static quality for {quality_basis!r}"
            )
        reasons: list[str] = []
        if spec.provider != OLLAMA_CLOUD_PROVIDER:
            reasons.append("wrong_provider")
        if spec.health != MODEL_HEALTH_HEALTHY:
            reasons.append("unhealthy")
        if not spec.supports(requirements):
            reasons.append("missing_capabilities")
        score = role_quality
        if reputation is not None:
            score = (
                _STATIC_QUALITY_WEIGHT * role_quality
                + _LOCAL_REPUTATION_WEIGHT * reputation
            )
        return RoleMarketCandidate(
            model=spec.name,
            family=spec.family,
            provider=spec.provider,
            health=spec.health,
            role_quality=role_quality,
            reputation=reputation,
            score=score,
            eligible=not reasons,
            ineligibility_reasons=tuple(reasons),
        )

    def _prescribed_models(
        self,
        role: str,
        requirements: frozenset[str],
    ) -> tuple[str, ...]:
        if role not in SAFETY_LOCKED_ROLES:
            return ()
        try:
            return self._router.select(role, requirements).models
        except (KeyError, NoEligibleModel):
            return ()


def _require_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _configured_safety_models(role: str) -> tuple[str, ...]:
    if role == "vision":
        return ("qwen3.5", "gemma4:31b")
    return _ROLE_CHAINS.get(role, ())
