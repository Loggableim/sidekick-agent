"""Contract tests for the browser-facing Google Gemini OAuth integration."""

from pathlib import Path

import web.api.oauth as oauth


def test_google_start_is_non_blocking_and_profile_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(oauth, "_get_active_profile_home", lambda: tmp_path)
    def publish_url(flow_id, *_args):
        oauth._OAUTH_FLOWS[flow_id]["auth_url"] = "https://accounts.google.com/o/oauth2/auth?state=test&code_challenge=test"
    monkeypatch.setattr(oauth, "_spawn_google_oauth_worker", publish_url)
    oauth._OAUTH_FLOWS.clear()

    payload = oauth.start_onboarding_oauth_flow({"provider": "google-gemini-cli"})
    assert payload["status"] == "pending"
    assert payload["provider"] == "google-gemini-cli"
    assert payload["flow_id"]
    assert payload["auth_url"].startswith("https://accounts.google.com/")
    assert "access_token" not in payload
    assert "refresh_token" not in payload

    try:
        oauth.start_onboarding_oauth_flow({"provider": "google-gemini-cli"})
    except ValueError as exc:
        assert "already in progress" in str(exc)
    else:
        raise AssertionError("parallel Google OAuth flow was accepted")


def test_google_cancel_drops_pending_flow_without_secrets(monkeypatch, tmp_path):
    monkeypatch.setattr(oauth, "_get_active_profile_home", lambda: tmp_path)
    monkeypatch.setattr(oauth, "_spawn_google_oauth_worker", lambda *_args: None)
    oauth._OAUTH_FLOWS.clear()
    started = oauth.start_onboarding_oauth_flow({"provider": "google-gemini-cli"})

    result = oauth.cancel_onboarding_oauth_flow({"provider": "google-gemini-cli", "flow_id": started["flow_id"]})
    assert result["status"] == "cancelled"
    assert "access_token" not in result
    assert "refresh_token" not in result


def test_google_webui_contract_surfaces_exist():
    routes = Path("web/api/routes.py").read_text(encoding="utf-8")
    panels = Path("web/static/panels.js").read_text(encoding="utf-8")
    onboarding = Path("web/static/onboarding.js").read_text(encoding="utf-8")
    assert "/api/oauth/google/start" in routes
    assert "/api/oauth/google/status" in routes
    assert "/api/oauth/google/disconnect" in routes
    assert "startGoogleGeminiOAuth" in panels
    assert "oauth_email" in panels
    assert "startGoogleGeminiOnboardingOAuth" in onboarding
