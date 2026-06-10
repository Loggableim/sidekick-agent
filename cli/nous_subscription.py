"""Helpers for Nous subscription managed-tool capabilities — no-op after migration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, Optional


@dataclass(frozen=True)
class NousFeatureState:
    key: str
    label: str
    included_by_default: bool
    available: bool
    active: bool
    managed_by_nous: bool
    direct_override: bool
    toolset_enabled: bool
    current_provider: str = ""
    explicit_configured: bool = False


@dataclass(frozen=True)
class NousSubscriptionFeatures:
    subscribed: bool
    nous_auth_present: bool
    provider_is_nous: bool
    features: Dict[str, NousFeatureState]

    @property
    def web(self) -> NousFeatureState:
        return self.features["web"]

    @property
    def image_gen(self) -> NousFeatureState:
        return self.features["image_gen"]

    @property
    def tts(self) -> NousFeatureState:
        return self.features["tts"]

    @property
    def browser(self) -> NousFeatureState:
        return self.features["browser"]

    @property
    def modal(self) -> NousFeatureState:
        return self.features["modal"]

    def items(self) -> Iterable[NousFeatureState]:
        ordered = ("web", "image_gen", "tts", "browser", "modal")
        for key in ordered:
            yield self.features[key]


_NO_SUBSCRIPTION = NousSubscriptionFeatures(
    subscribed=False,
    nous_auth_present=False,
    provider_is_nous=False,
    features={},
)


def get_nous_subscription_features(
    config: Optional[Dict[str, object]] = None,
) -> NousSubscriptionFeatures:
    return _NO_SUBSCRIPTION


def apply_nous_managed_defaults(
    config: Dict[str, object],
    *,
    enabled_toolsets: Optional[Iterable[str]] = None,
) -> set[str]:
    return set()


def get_gateway_eligible_tools(
    config: Optional[Dict[str, object]] = None,
) -> tuple[list[str], list[str], list[str]]:
    return [], [], []


def apply_gateway_defaults(
    config: Dict[str, object],
    tool_keys: list[str],
) -> set[str]:
    return set()


def prompt_enable_tool_gateway(config: Dict[str, object]) -> set[str]:
    return set()

