"""Tests for the panels.js lazy loader (backlog item 12).

panels.js is ~517 KB and was loaded on every page view. It is now fetched on
the first switchPanel() call through web/static/panels-loader.js.

The skill `lazy-load-panels` records that a previous attempt was reverted
because a parse error in ui.js was misattributed to it. The prerequisite — a
green `node --check` sweep enforced in CI — is covered by item 39
(scripts/check_webui_js.py + the js-parse CI job).
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "web" / "static" / "index.html"
LOADER_JS = ROOT / "web" / "static" / "panels-loader.js"
SW_JS = ROOT / "web" / "static" / "sw.js"


def test_index_no_longer_loads_panels_eagerly() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'src="/static/panels.js' not in index_html, (
        "panels.js must not be a static script tag anymore"
    )
    assert 'src="/static/panels-loader.js' in index_html, "loader shim missing"


def test_loader_defers_and_delegates() -> None:
    loader = LOADER_JS.read_text(encoding="utf-8")

    # The loader must inject the real file only when called.
    assert "document.createElement('script')" in loader
    assert "/static/panels.js" in loader
    # It must cache the promise so a second switch does not re-fetch.
    assert "_panelsPromise" in loader
    # And it must delegate to the real implementation once loaded.
    assert "window.switchPanel" in loader
    # A failed load must be retryable rather than permanent.
    assert "_panelsPromise = null" in loader


def test_loader_preserves_the_version_token() -> None:
    loader = LOADER_JS.read_text(encoding="utf-8")
    assert "__WEBUI_VERSION__" in loader, (
        "the loader must keep the cache-busting version token"
    )


def test_service_worker_precaches_the_loader_not_panels() -> None:
    sw = SW_JS.read_text(encoding="utf-8")
    assert "./static/panels-loader.js" in sw
    assert "./static/panels.js" not in sw, (
        "the SW must not pre-cache the lazily loaded file"
    )
