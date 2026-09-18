"""Tests for the service worker shell strategy (backlog item 19).

Shell assets used network-first, so every load waited on the network even when
the asset was already cached. They now use stale-while-revalidate: the cached
copy is served immediately and refreshed in the background.

This is safe because shell asset URLs carry `?v=<webui-version>`; a changed
file gets a new token, so the new URL is a cache miss.
"""
from __future__ import annotations

from pathlib import Path

SW_JS = Path(__file__).resolve().parents[1] / "web" / "static" / "sw.js"


def _shell_block() -> str:
    source = SW_JS.read_text(encoding="utf-8")
    start = source.index("// Shell assets: stale-while-revalidate")
    end = source.index("});", source.index("event.respondWith", start))
    return source[start:end]


def test_shell_assets_use_stale_while_revalidate() -> None:
    block = _shell_block()

    # The cache is consulted first ...
    assert "caches.match(event.request).then((cached)" in block
    # ... and served immediately when present.
    assert "if (cached)" in block
    assert "return cached;" in block
    # ... while the network refresh runs in the background.
    assert "event.waitUntil(network)" in block


def test_offline_fallback_is_preserved() -> None:
    block = _shell_block()
    assert "status: 503" in block, "the offline fallback must stay"
    assert "Offline" in block


def test_network_result_still_updates_the_cache() -> None:
    block = _shell_block()
    assert "cache.put(event.request, clone)" in block


def test_shell_asset_urls_stay_versioned() -> None:
    """SWR is only safe while every shell URL carries the version token."""
    source = SW_JS.read_text(encoding="utf-8")
    assert "const VQ = '?v=__WEBUI_VERSION__';" in source

    shell_start = source.index("const SHELL_ASSETS = [")
    shell_end = source.index("];", shell_start)
    shell_block = source[shell_start:shell_end]
    for line in shell_block.splitlines():
        entry = line.strip()
        if not entry.startswith("'./static/") or not entry.endswith("',"):
            continue
        # Binary assets (favicons, manifest) carry no token and are not
        # version-sensitive; every JS/CSS entry must.
        if entry.endswith((".js' + VQ,", ".css' + VQ,")):
            continue
        assert entry.endswith(("' + VQ,", ".svg',", ".png',", "manifest.json',")), (
            f"unversioned shell entry: {entry}"
        )
