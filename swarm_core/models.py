"""Provider-agnostic model values and the Swarm model registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
from typing import Any, Iterable, Mapping


OLLAMA_CLOUD_PROVIDER = "ollama-cloud"


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
    name: str
    family: str
    capabilities: frozenset[str]
    provider: str = OLLAMA_CLOUD_PROVIDER

    def supports(self, requirements: Iterable[str]) -> bool:
        return set(requirements).issubset(self.capabilities)


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
        if not self.context:
            return self.prompt
        context = json.dumps(self.context, sort_keys=True, ensure_ascii=False)
        return f"{self.prompt}\n\nContext:\n{context}"


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
    ),
    ModelSpec(
        "deepseek-v4-pro",
        "deepseek-v4",
        frozenset({"reasoning", "planning", "structured-output"}),
    ),
    ModelSpec(
        "kimi-k2.6",
        "kimi-k2",
        frozenset({"reasoning", "planning", "structured-output"}),
    ),
    ModelSpec(
        "minimax-m3",
        "minimax-m3",
        frozenset({"building", "critique", "reasoning", "structured-output"}),
    ),
    ModelSpec(
        "glm-5.2",
        "glm-5",
        frozenset({"coding", "review", "reasoning", "structured-output"}),
    ),
    ModelSpec(
        "kimi-k2.7-code",
        "kimi-k2",
        frozenset({"coding", "review", "reasoning", "structured-output"}),
    ),
    ModelSpec(
        "nemotron-3-super",
        "nemotron-3",
        frozenset({"integration", "referee", "reasoning", "structured-output"}),
    ),
    ModelSpec(
        "qwen3.5",
        "qwen3",
        frozenset({"vision", "reasoning", "structured-output"}),
    ),
    ModelSpec(
        "gemma4:31b",
        "gemma4",
        frozenset({"vision", "reasoning", "structured-output"}),
    ),
)


class ModelRegistry:
    """Describe known models and optionally constrain them to a live catalog."""

    def __init__(self, catalog: Iterable[str] | None = None) -> None:
        self._models = {spec.name: spec for spec in _MODEL_SPECS}
        self._catalog = None if catalog is None else frozenset(catalog)

    def get(self, name: str) -> ModelSpec:
        try:
            return self._models[name]
        except KeyError as exc:
            raise KeyError(f"Unknown Swarm model: {name}") from exc

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
