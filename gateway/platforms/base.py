"""Forwarder: gateway.platforms.base -> runtime.gateway.platforms.base."""
from __future__ import annotations
from runtime.gateway.platforms.base import *  # noqa: F401, F403


async def cleanup_image_cache():
    pass


async def cleanup_document_cache():
    pass
