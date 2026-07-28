"""Role-specific Ollama Cloud model routing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .models import ModelRegistry, OLLAMA_CLOUD_PROVIDER


_ROLE_CHAINS = {
    "default": ("deepseek-v4-flash", "deepseek-v4-pro"),
    "scout": ("deepseek-v4-flash", "deepseek-v4-pro"),
    "planner": ("deepseek-v4-pro", "kimi-k2.6"),
    # Kimi is an independently dispatched challenger only when the workflow
    # records an explicit conflict.  It is not called on a successful normal
    # planner result; the same Kimi entry after Pro remains the permitted
    # provider/schema fallback chain.
    "planner_challenger": ("kimi-k2.6",),
    "planner_arbitrator": ("deepseek-v4-pro", "kimi-k2.6"),
    "builder": ("minimax-m3",),
    "critic": ("minimax-m3",),
    "coding": ("glm-5.2",),
    "review_a": ("glm-5.2",),
    "review_b": ("kimi-k2.7-code",),
    "integrator": ("nemotron-3-super",),
    "referee": ("nemotron-3-super",),
}


class NoEligibleModel(LookupError):
    """The requested role has no model in the available Ollama catalog."""


@dataclass(frozen=True)
class ModelSelection:
    models: tuple[str, ...]
    provider: str
    family: str

    @property
    def model(self) -> str:
        return self.models[0]

    @property
    def fallback_chain(self) -> tuple[str, ...]:
        return self.models[1:]


class ModelRouter:
    def __init__(self, registry: ModelRegistry) -> None:
        self.registry = registry

    def select(self, role: str, requirements: Iterable[str]) -> ModelSelection:
        required = frozenset(requirements)
        if role == "vision":
            chain = self._vision_chain(required)
        else:
            try:
                configured_chain = _ROLE_CHAINS[role]
            except KeyError as exc:
                raise KeyError(f"Unknown Swarm role: {role}") from exc
            primary = configured_chain[0]
            if role in {"default", "scout"} and (
                not self.registry.is_available(primary)
                or not self.registry.get(primary).supports(required)
            ):
                chain = ()
            else:
                chain = tuple(
                    name
                    for name in configured_chain
                    if self.registry.is_available(name)
                    and self.registry.get(name).supports(required)
                )
        if not chain:
            raise NoEligibleModel(
                f"No Ollama Cloud model for role {role!r} and requirements "
                f"{sorted(required)!r}"
            )
        primary = self.registry.get(chain[0])
        return ModelSelection(chain, OLLAMA_CLOUD_PROVIDER, primary.family)

    def select_review_pair(self) -> tuple[ModelSelection, ModelSelection]:
        first = self.select("review_a", {"review", "structured-output"})
        second = self.select("review_b", {"review", "structured-output"})
        if first.family == second.family:
            raise RuntimeError("Independent review routes must use distinct families")
        return first, second

    def _vision_chain(self, requirements: frozenset[str]) -> tuple[str, ...]:
        required = requirements | {"vision"}
        names = (
            ("qwen3.5", "gemma4:31b")
            if self.registry.is_available("qwen3.5")
            else ("gemma4:31b",)
        )
        return tuple(
            name
            for name in names
            if self.registry.is_available(name)
            and self.registry.get(name).supports(required)
        )
