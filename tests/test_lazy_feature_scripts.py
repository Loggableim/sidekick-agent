"""Tests for the on-demand feature-script loader (backlog item 13).

browser.js, gmail.js, discord.js, discord-chat.js, agents.js, swarm.js and
onboarding.js total ~553 KB and were all loaded on every page view. They are
now fetched by web/static/feature-loader.js when their panel opens.

enhancements.js stays eager: it registers its own DOMContentLoaded handler.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "web" / "static" / "index.html"
LOADER_JS = ROOT / "web" / "static" / "feature-loader.js"
SW_JS = ROOT / "web" / "static" / "sw.js"

LAZY = (
    "browser.js",
    "gmail.js",
    "discord.js",
    "discord-chat.js",
    "agents.js",
    "swarm.js",
    "onboarding.js",
)


def test_index_no_longer_loads_feature_scripts_eagerly() -> None:
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    for name in LAZY:
        assert f'src="/static/{name}' not in index_html, (
            f"{name} must not be a static script tag anymore"
        )
    assert 'src="/static/feature-loader.js' in index_html, "loader shim missing"


def test_enhancements_stays_eager() -> None:
    """It registers a DOMContentLoaded handler, so it must load with the page."""
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    assert 'src="/static/enhancements.js' in index_html


def test_loader_maps_panels_to_scripts() -> None:
    loader = LOADER_JS.read_text(encoding="utf-8")

    for name in LAZY:
        assert name in loader, f"{name} missing from the loader map"
    # Panel -> file mapping for the panels that own a script.
    for panel in ("browser", "gmail", "discord", "agents", "swarm"):
        assert f"{panel}:" in loader, f"panel {panel} missing from the map"


def test_loader_caches_and_retries() -> None:
    loader = LOADER_JS.read_text(encoding="utf-8")

    assert "_loaded" in loader, "the loader must remember loaded files"
    assert "_promises" in loader, "the loader must cache in-flight loads"
    assert "_promises[file] = null" in loader, "a failed load must be retryable"
    assert "__WEBUI_VERSION__" in loader, "the version token must be preserved"


def test_loader_reinstalls_its_switchpanel_wrapper() -> None:
    """agents.js replaces window.switchPanel, so one install would be lost."""
    loader = LOADER_JS.read_text(encoding="utf-8")
    assert "installUntilStable" in loader
    assert "__sidekickFeatureWrapper" in loader


def test_service_worker_no_longer_precaches_lazy_scripts() -> None:
    sw = SW_JS.read_text(encoding="utf-8")
    for name in LAZY:
        assert f"./static/{name}' + VQ" not in sw, (
            f"{name} must not be pre-cached: it is lazy-loaded"
        )
    assert "./static/feature-loader.js" in sw
