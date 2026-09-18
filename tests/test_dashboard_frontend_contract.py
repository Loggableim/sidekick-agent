from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = ROOT / "web" / "static" / "index.html"
BOOT_JS = ROOT / "web" / "static" / "boot.js"
ONBOARDING_JS = ROOT / "web" / "static" / "onboarding.js"


def check_dashboard_frontend_contract() -> None:
    """Assert the core WebUI bootstrap contract stays stable."""
    index_html = INDEX_HTML.read_text(encoding="utf-8")
    assert index_html.index("static/api-auth.js") < index_html.index("static/boot.js")

    boot_js = BOOT_JS.read_text(encoding="utf-8")
    assert "if(k && k.startsWith('sidekick-'))" in boot_js
    assert "localStorage.setItem(keys[i].replace('sidekick-','sidekick-'), v);" in boot_js
    assert "window.toggleFileTreePanel=function(force){return toggleWorkspacePanel(force);};" in boot_js

    onboarding_js = ONBOARDING_JS.read_text(encoding="utf-8")
    assert "openai-codex" in onboarding_js
    assert "oauth_login_codex" in onboarding_js
    assert "startCodexOAuth" in onboarding_js


def test_dashboard_frontend_contract() -> None:
    check_dashboard_frontend_contract()


def test_index_has_no_cdn_connection_hint() -> None:
    """The CDN is gone (item 14), so the preconnect hint must be gone too.

    Item 15 added the hint to warm the cdn.jsdelivr.net connection. Once the
    assets became self-hosted the hint would only open a useless socket.
    """
    index_html = INDEX_HTML.read_text(encoding="utf-8")

    assert 'rel="preconnect" href="https://cdn.jsdelivr.net"' not in index_html
    assert 'rel="dns-prefetch" href="https://cdn.jsdelivr.net"' not in index_html
    # And the assets it used to warm are served locally.
    assert "/static/vendor/prism/prism-core.min.js" in index_html
    assert "/static/vendor/xterm/xterm.js" in index_html
    assert "/static/vendor/katex/katex.min.css" in index_html

