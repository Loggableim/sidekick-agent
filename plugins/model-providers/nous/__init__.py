"""Nous Research model provider plugin for Sidekick Agent.

Registers the "Nous Research" provider entry and tags API calls
with the correct product identifier for telemetry / routing.
"""

display_name = "Nous Research"
description = "Nous Research — Hermes model family"


def get_extra_body() -> dict:
    """Return extra body kwargs for models served via Nous Portal.

    Includes the product tag so the upstream routing layer can
    distinguish Sidekick Agent traffic from other clients.
    """
    return {"tags": ["product=sidekick-agent"]}


__all__ = [
    "display_name",
    "description",
    "get_extra_body",
]
