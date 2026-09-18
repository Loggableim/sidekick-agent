"""Tests for startup precompression of the shell assets (backlog item 10).

A cold shell load pulls ~28 assets and each first request used to pay its own
gzip compression (~74 ms in total). The startup hook warms the cache so the
first request per asset is a cache hit.
"""
from __future__ import annotations

import re
import time

from cli import web_server


def test_precompress_list_covers_the_service_worker_shell() -> None:
    """Everything the SW pre-caches must also be pre-compressed.

    The reverse is not required: the precompress list may be broader (it warms
    panel CSS too, which the SW fetches on demand since backlog item 20).
    """
    sw_source = (web_server.WEB_DIST / "sw.js").read_text(encoding="utf-8")

    shell_start = sw_source.index("const SHELL_ASSETS = [")
    shell_end = sw_source.index("];", shell_start)
    shell_block = sw_source[shell_start:shell_end]
    shell_assets = set(re.findall(r"'\./static/([^']+)'", shell_block))

    # Binary assets (PNG/SVG icons) are already compressed and are served
    # without gzip, so they are not in the precompress list by design.
    text_assets = {name for name in shell_assets if name.endswith((".js", ".css", ".json"))}
    missing = sorted(name for name in text_assets if name not in web_server._PRECOMPRESS_ASSETS)
    assert not missing, f"SW pre-caches assets that are not pre-compressed: {missing}"


def test_precompress_warms_the_cache_and_removes_the_cost() -> None:
    original_cache = dict(web_server._GZIP_CACHE)
    original_bytes = web_server._GZIP_CACHE_BYTES
    try:
        web_server._GZIP_CACHE.clear()
        web_server._GZIP_CACHE_BYTES = 0

        web_server._precompress_shell_assets()
        warmed = len(web_server._GZIP_CACHE)
        assert warmed > 0, "precompression cached nothing"

        # A second pass over the same assets must be a pure cache hit.
        start = time.perf_counter()
        for name in web_server._PRECOMPRESS_ASSETS:
            path = web_server.WEB_DIST / name
            if path.is_file():
                web_server._gzip_cached(path, path.stat())
        warm_ms = (time.perf_counter() - start) * 1000

        assert warm_ms < 20, f"warm pass still costs {warm_ms:.1f} ms"
    finally:
        web_server._GZIP_CACHE.clear()
        web_server._GZIP_CACHE.update(original_cache)
        web_server._GZIP_CACHE_BYTES = original_bytes


def test_precompress_is_registered_as_a_startup_hook() -> None:
    handlers = [getattr(fn, "__name__", "") for fn in web_server.app.router.on_startup]
    assert "_start_shell_precompress" in handlers


def test_precompress_survives_missing_assets(monkeypatch, tmp_path) -> None:
    """A missing asset must be skipped, not crash the startup hook."""
    monkeypatch.setattr(
        web_server, "_PRECOMPRESS_ASSETS", ("does-not-exist.js", "ui.js")
    )
    web_server._precompress_shell_assets()  # must not raise
