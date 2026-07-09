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
    assert "if(k && k.startsWith('hermes-'))" in boot_js
    assert "localStorage.setItem(keys[i].replace('hermes-','sidekick-'), v);" in boot_js
    assert "window.toggleFileTreePanel=function(force){return toggleWorkspacePanel(force);};" in boot_js

    onboarding_js = ONBOARDING_JS.read_text(encoding="utf-8")
    assert "openai-codex" in onboarding_js
    assert "oauth_login_codex" in onboarding_js
    assert "startCodexOAuth" in onboarding_js


def test_dashboard_frontend_contract() -> None:
    check_dashboard_frontend_contract()
