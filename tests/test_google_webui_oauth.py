"""Contract tests for the browser-facing Google Gemini OAuth integration."""

from pathlib import Path
import threading
import urllib.request

import pytest

import web.api.oauth as oauth
from runtime import google_oauth


@pytest.mark.parametrize("creds,expected", [
    (None, "not_connected"),
    (google_oauth.GoogleCredentials("access-secret", "refresh-secret", 0), "connected"),
    (google_oauth.GoogleCredentials("access-secret", "", 0), "expired"),
])
def test_google_provider_status_never_falls_through_to_config_yaml(monkeypatch, creds, expected):
    from web.api import providers
    monkeypatch.setattr(providers, "get_config", lambda: {})
    monkeypatch.setattr(providers, "_PROVIDER_DISPLAY", {"google-gemini-cli": "Gemini CLI"})
    monkeypatch.setattr(providers, "_PROVIDER_MODELS", {})
    monkeypatch.setattr(providers, "_OAUTH_PROVIDERS", {"google-gemini-cli"})
    monkeypatch.setattr(providers, "_provider_has_key", lambda _: False)
    monkeypatch.setattr(google_oauth, "load_credentials", lambda: creds)
    result = providers.get_providers()["providers"][0]
    assert result["key_source"] == "oauth"
    assert result["auth_state"] == expected
    assert result["auth_error"] is None if expected == "connected" else result["auth_error"]
    assert "access-secret" not in str(result)
    assert "refresh-secret" not in str(result)


@pytest.mark.parametrize("outcome", ["available", "denied", "empty"])
def test_google_quota_is_numeric_and_reports_safe_failure_reason(monkeypatch, outcome):
    from runtime import google_code_assist
    from web.api import providers
    creds = google_oauth.GoogleCredentials("access-secret", "refresh-secret", 0, managed_project_id="managed")
    monkeypatch.setattr(google_oauth, "load_credentials", lambda: creds)
    monkeypatch.setattr(google_oauth, "get_valid_access_token", lambda: "access-secret")

    def retrieve(token, *, project_id):
        assert token == "access-secret"
        assert project_id == "managed"
        if outcome == "denied":
            raise google_code_assist.CodeAssistError(
                "SUBSCRIPTION_REQUIRED access-secret", code="code_assist_http_403",
            )
        return [] if outcome == "empty" else [google_code_assist.QuotaBucket("gemini", remaining_fraction=0.75)]

    monkeypatch.setattr(google_code_assist, "retrieve_user_quota", retrieve)
    result = providers.get_provider_quota("google-gemini-cli")
    if outcome == "available":
        assert result["account_limits"]["windows"][0]["remaining_percent"] == 75
    else:
        assert result["status"] == "unavailable"
        assert result["error_code"] == ("subscription_required" if outcome == "denied" else "quota_unavailable")
    assert "access-secret" not in str(result)
    assert "refresh-secret" not in str(result)


def test_google_card_renders_oauth_and_quota_details():
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js required")
    script = r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
class Element {
  constructor(){this.children=[];this.dataset={};this.style={};this.events={};}
  appendChild(child){this.children.push(child);return child;}
  setAttribute(){}
  addEventListener(type,fn){this.events[type]=fn;}
  replaceChildren(...children){this.children=children;}
}
const source=fs.readFileSync('web/static/panels.js','utf8');
const start=source.indexOf('function _buildProviderCard(p)');
const end=source.indexOf('\nfunction ',start+1);
let payload={status:'available',account_limits:{windows:[{remaining_percent:75}]}};
let rendered;
const context={document:{createElement:()=>new Element()},esc:x=>x,
  _providerText:(key,fallback)=>key==='providers_status_configured'?'API key configured':fallback,
  api:async()=>payload,_buildProviderQuotaCard:q=>{rendered=q;return new Element();}};
context.window=context;
vm.createContext(context);vm.runInContext(source.slice(start,end),context);
const card=context._buildProviderCard({id:'google-gemini-cli',display_name:'Gemini CLI',is_oauth:true,has_key:true,key_source:'oauth',auth_state:'connected',models:[]});
assert.match(card.children[0].innerHTML,/Google OAuth verbunden/);
assert.doesNotMatch(card.children[0].innerHTML,/API key configured/);
const body=card.children[1];
assert.match(body.children[0].textContent,/Kein API-Schlüssel/);
const actions=body.children.find(x=>x.className==='provider-card-actions');
const quota=actions.children.find(x=>x.textContent==='Quota prüfen');
(async()=>{
  await quota.events.click();assert.equal(rendered,payload);assert.equal(quota.disabled,false);
  payload={status:'unavailable',message:'Google verweigert Zugriff'};
  await quota.events.click();assert.equal(rendered,payload);
})().catch(e=>{console.error(e);process.exitCode=1});
"""
    result = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


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


def test_google_poll_expires_without_exposing_flow_secrets():
    oauth._OAUTH_FLOWS.clear()
    oauth._OAUTH_FLOWS["expired-flow"] = {
        "provider": "google-gemini-cli", "status": "pending", "expires_at": 0,
        "updated_at": 0, "access_token": "secret", "refresh_token": "secret",
    }
    result = oauth.poll_onboarding_oauth_flow("expired-flow")
    assert result["status"] == "expired"
    assert "access_token" not in result
    assert "refresh_token" not in result


def test_google_token_persistence_rejects_incomplete_response():
    with pytest.raises(google_oauth.GoogleOAuthError) as exc:
        google_oauth._persist_token_response({"access_token": "only-access"})
    assert exc.value.code == "google_oauth_incomplete_token_response"


def test_google_callback_rejects_wrong_state():
    server, _ = google_oauth._bind_callback_server(0)
    port = server.server_address[1]
    google_oauth._OAuthCallbackHandler.expected_state = "expected"
    google_oauth._OAuthCallbackHandler.captured_code = None
    google_oauth._OAuthCallbackHandler.captured_error = None
    google_oauth._OAuthCallbackHandler.ready = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(Exception):
            urllib.request.urlopen(
                f"http://127.0.0.1:{port}/oauth2callback?state=wrong&code=unsafe",
                timeout=3,
            )
        assert google_oauth._OAuthCallbackHandler.captured_error == "state_mismatch"
        assert google_oauth._OAuthCallbackHandler.captured_code is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_google_disconnect_clears_runtime_and_pool(monkeypatch):
    calls = []
    monkeypatch.setattr("runtime.google_oauth.load_credentials", lambda: object())
    monkeypatch.setattr("runtime.google_oauth.clear_credentials", lambda: calls.append("runtime"))
    monkeypatch.setattr("runtime.credential_pool.write_credential_pool", lambda provider, entries: calls.append((provider, entries)))
    result = oauth.disconnect_google_oauth()
    assert result["disconnected"] is True
    assert calls == ["runtime", ("google-gemini-cli", [])]
