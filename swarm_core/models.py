"""Provider-agnostic model values and the Swarm model registry."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
import json
import math
from types import MappingProxyType
from typing import Any, Iterable, Mapping


OLLAMA_CLOUD_PROVIDER = "ollama-cloud"
MODEL_HEALTH_HEALTHY = "healthy"
MODEL_HEALTH_UNAVAILABLE = "unavailable"
MODEL_HEALTH_UNVERIFIED = "unverified"
_MODEL_HEALTH_VALUES = frozenset(
    {
        MODEL_HEALTH_HEALTHY,
        MODEL_HEALTH_UNAVAILABLE,
        MODEL_HEALTH_UNVERIFIED,
    }
)
_SWARM_RESPONSE_FIELD_TYPES = MappingProxyType(
    {
        "work": "string",
        "evidence": "array",
        "decision": "string",
        "approved": "boolean",
    }
)


@dataclass(frozen=True)
class ModelCatalogSnapshot:
    """One explicitly refreshed provider catalog, safe to persist project-locally.

    The snapshot deliberately contains availability metadata only.  It never
    carries API credentials, prompts, model responses, or cost information.
    """

    provider: str
    models: tuple[str, ...]
    healthy: bool
    source: str
    refreshed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        provider = str(self.provider).strip().lower()
        source = str(self.source).strip()
        if not provider:
            raise ValueError("Catalog provider must be non-empty")
        if not source:
            raise ValueError("Catalog source must be non-empty")
        if not isinstance(self.healthy, bool):
            raise TypeError("Catalog health must be a bool")
        models: list[str] = []
        for model in self.models:
            normalized = str(model).strip()
            if not normalized:
                raise ValueError("Catalog model names must be non-empty")
            if normalized not in models:
                models.append(normalized)
        refreshed_at = self.refreshed_at
        if refreshed_at.tzinfo is None:
            raise ValueError("Catalog refresh timestamp must be timezone-aware")
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "models", tuple(models))


@dataclass(frozen=True)
class ModelSpec:
    """Immutable provider metadata for a routeable Ollama Cloud model.

    Static capabilities and context budgets are conservative provider-declared
    limits.  ``health`` is derived only from an explicitly supplied catalog;
    ``role_quality`` is a routing-policy baseline, not learned reputation.
    """

    name: str
    family: str
    capabilities: frozenset[str]
    provider: str = OLLAMA_CLOUD_PROVIDER
    tools: bool = False
    vision: bool = False
    thinking: bool = False
    json_capable: bool = False
    context_budget: int | None = None
    health: str = MODEL_HEALTH_UNVERIFIED
    role_quality: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        name = str(self.name).strip()
        family = str(self.family).strip()
        provider = str(self.provider).strip().lower()
        if not name:
            raise ValueError("Model name must be non-empty")
        if not family:
            raise ValueError("Model family must be non-empty")
        if not provider:
            raise ValueError("Model provider must be non-empty")

        capabilities = frozenset(
            str(capability).strip().lower()
            for capability in self.capabilities
            if str(capability).strip()
        )
        if not capabilities:
            raise ValueError("Model capabilities must be non-empty")
        for field_name in ("tools", "vision", "thinking", "json_capable"):
            if not isinstance(getattr(self, field_name), bool):
                raise TypeError(f"Model {field_name} must be a bool")

        context_budget = self.context_budget
        if context_budget is not None and (
            isinstance(context_budget, bool)
            or not isinstance(context_budget, int)
            or context_budget <= 0
        ):
            raise ValueError("Model context budget must be a positive integer")

        health = str(self.health).strip().lower()
        if health not in _MODEL_HEALTH_VALUES:
            raise ValueError(f"Unknown model health: {self.health!r}")

        quality: dict[str, float] = {}
        for role, score in self.role_quality.items():
            normalized_role = str(role).strip().lower()
            if not normalized_role:
                raise ValueError("Model role quality role must be non-empty")
            if isinstance(score, bool) or not isinstance(score, (int, float)):
                raise TypeError("Model role quality must be numeric")
            normalized_score = float(score)
            if (
                not math.isfinite(normalized_score)
                or not 0.0 <= normalized_score <= 1.0
            ):
                raise ValueError("Model role quality must be between 0 and 1")
            quality[normalized_role] = normalized_score

        object.__setattr__(self, "name", name)
        object.__setattr__(self, "family", family)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "capabilities", capabilities)
        object.__setattr__(self, "health", health)
        object.__setattr__(
            self,
            "role_quality",
            MappingProxyType(dict(sorted(quality.items()))),
        )

    def supports(self, requirements: Iterable[str]) -> bool:
        return all(
            self._supports_requirement(requirement) for requirement in requirements
        )

    def quality_for(self, role: str) -> float | None:
        """Return the immutable routing baseline for one role, if configured."""
        return self.role_quality.get(str(role).strip().lower())

    def _supports_requirement(self, requirement: object) -> bool:
        normalized = str(requirement).strip().lower()
        if normalized == "tools":
            return self.tools
        if normalized == "vision":
            return self.vision
        if normalized == "thinking":
            return self.thinking
        if normalized in {"json", "json-output", "structured-output"}:
            return self.json_capable or normalized in self.capabilities
        return normalized in self.capabilities


@dataclass(frozen=True)
class ModelRequest:
    run_id: str
    role: str
    model: str
    prompt: str
    context: Mapping[str, Any]
    required_fields: tuple[str, ...] = ("work", "evidence", "decision")
    provider: str = OLLAMA_CLOUD_PROVIDER

    def render_prompt(self) -> str:
        """Render a stable prompt for Sidekick-compatible chat transports."""
        sections = [self.prompt]
        if self.context:
            context = json.dumps(self.context, sort_keys=True, ensure_ascii=False)
            sections.append(f"Context:\n{context}")
        field_descriptions = []
        for field_name in self.required_fields:
            name = str(field_name)
            encoded_name = json.dumps(name, ensure_ascii=False)
            field_type = _SWARM_RESPONSE_FIELD_TYPES.get(name)
            field_descriptions.append(
                f"{encoded_name} ({field_type})" if field_type else encoded_name
            )
        fields = ", ".join(field_descriptions)
        sections.append(
            "Output contract:\n"
            "Return exactly one JSON object. Do not include Markdown, code fences, or prose.\n"
            f"Required fields: {fields}."
        )
        return "\n\n".join(sections)


@dataclass(frozen=True)
class ModelResponse:
    model: str
    content: str
    data: Mapping[str, Any]


_MODEL_SPECS = (
    ModelSpec(
        "deepseek-v4-flash",
        "deepseek-v4",
        frozenset({"reasoning", "scouting", "structured-output"}),
        tools=True,
        thinking=True,
        json_capable=True,
        context_budget=1_000_000,
        role_quality={"default": 1.0, "scout": 1.0},
    ),
    ModelSpec(
        "deepseek-v4-pro",
        "deepseek-v4",
        frozenset({"reasoning", "planning", "scouting", "structured-output"}),
        tools=True,
        thinking=True,
        json_capable=True,
        context_budget=1_000_000,
        role_quality={"default": 0.9, "planner": 1.0, "scout": 0.9},
    ),
    ModelSpec(
        "kimi-k2.6",
        "kimi-k2",
        frozenset({"reasoning", "planning", "structured-output", "vision"}),
        tools=True,
        vision=True,
        thinking=True,
        json_capable=True,
        context_budget=256_000,
        role_quality={"planner": 0.9},
    ),
    ModelSpec(
        "minimax-m3",
        "minimax-m3",
        frozenset({"building", "critique", "reasoning", "structured-output", "vision"}),
        tools=True,
        vision=True,
        thinking=True,
        json_capable=True,
        context_budget=512_000,
        role_quality={"builder": 1.0, "critic": 1.0},
    ),
    ModelSpec(
        "glm-5.2",
        "glm-5",
        frozenset({"coding", "review", "reasoning", "structured-output"}),
        tools=True,
        thinking=True,
        json_capable=True,
        context_budget=976_000,
        role_quality={"coding": 1.0, "review_a": 1.0},
    ),
    ModelSpec(
        "kimi-k2.7-code",
        "kimi-k2",
        frozenset({"coding", "review", "reasoning", "structured-output", "vision"}),
        tools=True,
        vision=True,
        thinking=True,
        json_capable=True,
        context_budget=256_000,
        role_quality={"review_b": 1.0},
    ),
    ModelSpec(
        "nemotron-3-super",
        "nemotron-3",
        frozenset({"integration", "referee", "reasoning", "structured-output"}),
        tools=True,
        thinking=True,
        json_capable=True,
        context_budget=256_000,
        role_quality={"integrator": 1.0, "referee": 1.0},
    ),
    ModelSpec(
        "qwen3.5",
        "qwen3",
        frozenset({"vision", "reasoning", "structured-output"}),
        tools=True,
        vision=True,
        thinking=True,
        json_capable=True,
        context_budget=256_000,
        role_quality={"vision": 1.0},
    ),
    ModelSpec(
        "gemma4:31b",
        "gemma4",
        frozenset({"vision", "reasoning", "structured-output"}),
        tools=True,
        vision=True,
        thinking=True,
        json_capable=True,
        context_budget=256_000,
        role_quality={"vision": 0.9},
    ),
)


class ModelRegistry:
    """Describe known models and optionally constrain them to a live catalog."""

    def __init__(self, catalog: Iterable[str] | None = None) -> None:
        self._catalog = (
            None
            if catalog is None
            else frozenset(str(name).strip() for name in catalog if str(name).strip())
        )
        self._models = {
            spec.name: replace(spec, health=self._health_for(spec.name))
            for spec in _MODEL_SPECS
        }

    def _health_for(self, name: str) -> str:
        if self._catalog is None:
            return MODEL_HEALTH_UNVERIFIED
        if name in self._catalog:
            return MODEL_HEALTH_HEALTHY
        return MODEL_HEALTH_UNAVAILABLE

    def get(self, name: str) -> ModelSpec:
        try:
            return self._models[name]
        except KeyError as exc:
            raise KeyError(f"Unknown Swarm model: {name}") from exc

    def all_models(self) -> tuple[ModelSpec, ...]:
        """Return immutable known-model metadata without changing any route."""
        return tuple(self._models[spec.name] for spec in _MODEL_SPECS)

    def is_available(self, name: str) -> bool:
        if name not in self._models:
            return False
        if self._catalog is not None:
            return name in self._catalog
        # Qwen is deliberately opt-in: its route is enabled only by catalog proof.
        return name != "qwen3.5"

    def discover(self, requirements: Iterable[str]) -> tuple[ModelSpec, ...]:
        required = frozenset(requirements)
        return tuple(
            spec
            for spec in _MODEL_SPECS
            if self.is_available(spec.name) and spec.supports(required)
        )
