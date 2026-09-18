"""Tests for startup precompression of the shell assets (backlog item 10).

A cold shell load pulls ~28 assets and each first request used to pay its own
gzip compression (~74 ms in total). The startup hook warms the cache so the
first request per asset is a cache hit.
"""
from __future__ import annotations

import time

from cli import web_server


def test_precompress_list_matches_the_service_worker_shell() -> None:
    """The precompressed set must cover the assets sw.js pre-caches."""
    sw_source = (web_server.WEB_DIST / "sw.js").read_text(encoding="utf-8")

    missing = [
        name
        for name in web_server._PRECOMPRESS_ASSETS
        if f"./static/{name}'" not in sw_source and f"./static/{name}\"" not in sw_source
    ]
    assert not missing, f"precompressed assets missing from sw.js SHELL_ASSETS: {missing}"


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
