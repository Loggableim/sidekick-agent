from __future__ import annotations

import importlib
import json
from pathlib import Path


def test_webui_home_resolution_prefers_hermes_home(monkeypatch, tmp_path):
    hermes_home = tmp_path / "hermes"
    (hermes_home / "sidekick-agent").mkdir(parents=True)
    (hermes_home / "state.db").write_text("", encoding="utf-8")

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    from web.api import _home, agents, appstore, models, profiles, rollback, startup, state_sync

    importlib.reload(profiles)
    importlib.reload(appstore)
    importlib.reload(agents)
    importlib.reload(models)
    importlib.reload(rollback)
    importlib.reload(startup)
    importlib.reload(state_sync)

    assert _home.get_webui_home() == hermes_home
    assert profiles.get_active_hermes_home() == hermes_home
    assert appstore._ENV_FILE == hermes_home / ".env"
    assert appstore._CONFIG_FILE == hermes_home / "config.yaml"
    assert startup._agent_dir() == hermes_home / "sidekick-agent"
    assert rollback._hermes_home() == hermes_home

    monkeypatch.setattr(
        profiles,
        "get_active_hermes_home",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda _profile: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert models._get_profile_home("default") == hermes_home
    assert models._active_state_db_path() == hermes_home / "state.db"

    captured = {}

    class FakeSessionDB:
        def __init__(self, db_path):
            captured["db_path"] = Path(db_path)

        def close(self):
            return None

        def ensure_session(self, *args, **kwargs):
            return None

    monkeypatch.setattr("runtime._compat.shim_state.SessionDB", FakeSessionDB)

    db = state_sync._get_state_db()
    assert isinstance(db, FakeSessionDB)
    assert captured["db_path"] == hermes_home / "state.db"


def test_config_state_dir_uses_hermes_home_when_sidekick_home_missing(monkeypatch, tmp_path):
    hermes_home = tmp_path / "config-home"
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    import sys

    sys.modules.pop("web.api.config", None)
    config = importlib.import_module("web.api.config")

    assert config.STATE_DIR == hermes_home / "state" / "webui"


def test_agent_workspace_uses_hermes_home_when_sidekick_home_missing(monkeypatch, tmp_path):
    hermes_home = tmp_path / "agent-home"
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    import sys

    sys.modules.pop("web.api.agent_workspace", None)
    agent_workspace = importlib.import_module("web.api.agent_workspace")

    assert agent_workspace.HERMES_HOME == hermes_home
    assert agent_workspace.WORKSPACES_ROOT == hermes_home / "workspaces"


def test_evey_tools_uses_hermes_home_when_sidekick_home_missing(monkeypatch, tmp_path):
    hermes_home = tmp_path / "evey-home"
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    import sys

    sys.modules.pop("web.api.evey_tools", None)
    evey_tools = importlib.import_module("web.api.evey_tools")

    assert evey_tools.get_hermes_home() == hermes_home
    assert evey_tools.HERMES_HOME == hermes_home
    assert evey_tools.EVEY_DIR == hermes_home / "workspace" / "evey"


def test_routes_helpers_use_shared_webui_home_fallback(monkeypatch, tmp_path):
    hermes_home = tmp_path / "routes-home"
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    from web.api import profiles, routes

    monkeypatch.setattr(
        profiles,
        "get_active_hermes_home",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert routes._active_skills_dir() == hermes_home / "skills"
    assert routes._gateway_session_metadata_path() == hermes_home / "sessions" / "sessions.json"
    assert routes._llm_wiki_active_hermes_home() == hermes_home


def test_gateway_watcher_uses_shared_webui_home_fallback(monkeypatch, tmp_path):
    hermes_home = tmp_path / "watcher-home"
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    from web.api import gateway_watcher, profiles

    monkeypatch.setattr(
        profiles,
        "get_active_hermes_home",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert gateway_watcher._get_state_db_path() == hermes_home / "state.db"


def test_appstore_install_uses_active_profile_home_after_import(monkeypatch, tmp_path):
    import yaml

    import sys

    import_path_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    manifest_dir = active_home / "appstore"
    manifest_dir.mkdir(parents=True)
    (manifest_dir / "test-addon.json").write_text(
        json.dumps(
            {
                "key": "test-addon",
                "name": "Test Addon",
                "version": "1.0.0",
                "env_writes": {"TEST_APP_TOKEN": "token"},
                "config_changes": [
                    {"path": "model.default", "value": "gpt-4"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (active_home / "config.yaml").write_text("model:\n  default: gpt-oss\n", encoding="utf-8")

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("web.api.appstore", None)
    appstore = importlib.import_module("web.api.appstore")

    monkeypatch.setattr(appstore, "get_active_webui_home", lambda: active_home)
    monkeypatch.delenv("TEST_APP_TOKEN", raising=False)

    result = appstore.install_app("test-addon", {"TEST_APP_TOKEN": "secret-token"})

    assert result["success"] is True
    assert (active_home / ".env").read_text(encoding="utf-8").strip() == "TEST_APP_TOKEN=secret-token"
    assert yaml.safe_load((active_home / "config.yaml").read_text(encoding="utf-8"))["model"]["default"] == "gpt-4"
    installed = yaml.safe_load((active_home / "appstore" / ".installed.json").read_text(encoding="utf-8"))
    assert installed["test-addon"]["version"] == "1.0.0"
    assert not (import_path_home / ".env").exists()


def test_evey_tools_uses_active_profile_home_after_import(monkeypatch, tmp_path):
    import json as _json
    import sys

    import_path_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    data_dir = active_home / "workspace" / "evey" / "data"
    log_dir = active_home / "workspace" / "evey" / "logs"
    cache_dir = active_home / "workspace" / "evey" / "cache"
    data_dir.mkdir(parents=True)
    log_dir.mkdir(parents=True)
    cache_dir.mkdir(parents=True)
    (data_dir / "learnings.jsonl").write_text(
        _json.dumps({"task": "active-task"}) + "\n",
        encoding="utf-8",
    )
    (data_dir / "delegation-scores.jsonl").write_text(
        _json.dumps({"model": "active-model", "task_type": "code", "score": 9}) + "\n",
        encoding="utf-8",
    )
    (data_dir / "watchdog-state.json").write_text(
        _json.dumps({"last_heartbeat": 1, "total_heartbeats": 1, "last_activity": "old"}) + "\n",
        encoding="utf-8",
    )

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("web.api.evey_tools", None)
    evey_tools = importlib.import_module("web.api.evey_tools")

    monkeypatch.setattr(evey_tools, "get_active_webui_home", lambda: active_home, raising=False)

    status = evey_tools.get_status()
    heartbeat = evey_tools.watchdog_heartbeat("active-heartbeat")

    assert status["evey"]["learnings"] == 1
    assert status["evey"]["delegation_scores"] == 1
    assert heartbeat["status"] == "alive"
    watchdog = _json.loads((data_dir / "watchdog-state.json").read_text(encoding="utf-8"))
    assert watchdog["last_activity"] == "active-heartbeat"
    assert not (import_path_home / "workspace" / "evey" / "data" / "watchdog-state.json").exists()


def test_routes_use_active_profile_home_after_import(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from urllib.parse import urlencode

    import sys

    import_path_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    (active_home / "spaces" / "demo").mkdir(parents=True)
    (active_home / "spaces" / "demo" / "mail.json").write_text(
        json.dumps({"inboxes": ["work"]}),
        encoding="utf-8",
    )
    (active_home / "cockpit").mkdir(parents=True)
    media_file = active_home / "media.txt"
    media_file.write_text("active media\n", encoding="utf-8")

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("web.api.routes", None)
    routes = importlib.import_module("web.api.routes")

    monkeypatch.setattr(routes, "get_active_webui_home", lambda: active_home)
    monkeypatch.setattr(routes, "_workspace_slug_from_request", lambda *_args, **_kwargs: "demo")
    monkeypatch.setattr("web.api.auth.is_auth_enabled", lambda: False)

    served = {}

    def _fake_serve_file_bytes(handler, target, mime, disposition, cache_control, *, csp=None, inject_doctype=False):
        served["target"] = Path(target)
        served["mime"] = mime
        served["disposition"] = disposition
        return True

    monkeypatch.setattr(routes, "_serve_file_bytes", _fake_serve_file_bytes)
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload},
    )

    media_response = routes._handle_media(object(), SimpleNamespace(query=urlencode({"path": str(media_file)})))
    mail_response = routes._handle_mail_config_get(object(), SimpleNamespace(query=""))

    assert media_response is True
    assert served["target"] == media_file.resolve()
    assert mail_response["payload"]["config"] == {"inboxes": ["work"]}
    assert routes._cockpit_settings_path() == active_home / "cockpit" / ".cockpit_settings.json"


def test_mail_config_get_falls_back_to_legacy_space_yaml_gmail_accounts(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import sys

    active_home = tmp_path / "active-home"
    space_yaml = active_home / "spaces" / "demo" / "space.yaml"
    space_yaml.parent.mkdir(parents=True)
    space_yaml.write_text(
        json.dumps(
            {
                "gmail": {
                    "accounts": {
                        "work": {
                            "email": "ada@gmail.com",
                            "password": "app-password",
                            "default": True,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(active_home))

    sys.modules.pop("web.api.routes", None)
    routes = importlib.import_module("web.api.routes")

    monkeypatch.setattr(routes, "get_active_webui_home", lambda: active_home)
    monkeypatch.setattr(routes, "_workspace_slug_from_request", lambda *_args, **_kwargs: "demo")
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload},
    )

    response = routes._handle_mail_config_get(object(), SimpleNamespace(query=""))

    assert response["status"] == 200
    inbox = response["payload"]["config"]["inboxes"][0]
    assert inbox["id"] == "work"
    assert inbox["imap_host"] == "imap.gmail.com"
    assert inbox["smtp_host"] == "smtp.gmail.com"
    assert inbox["provider"] == "Gmail"


def test_mail_setup_post_saves_synthesized_config_and_activates_mail_app(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import sys

    active_home = tmp_path / "active-home"
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(active_home))

    sys.modules.pop("web.api.routes", None)
    routes = importlib.import_module("web.api.routes")

    activations = []

    monkeypatch.setattr(routes, "get_active_webui_home", lambda: active_home)
    monkeypatch.setattr(routes, "_workspace_slug_from_request", lambda *_args, **_kwargs: "demo")
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload},
    )

    import web.api.appstore as appstore

    monkeypatch.setattr(
        appstore,
        "_set_space_app_active",
        lambda space_slug, app_key, active: activations.append((space_slug, app_key, active)) or True,
    )

    response = routes._handle_mail_setup_post(
        object(),
        SimpleNamespace(query=""),
        {
            "email": "ada@gmail.com",
            "password": "app-password",
            "account_id": "work",
            "label": "Arbeitsmail",
            "activate": True,
        },
    )

    assert response["status"] == 200
    payload = response["payload"]
    assert payload["success"] is True
    assert payload["provider"] == "Gmail"
    assert payload["space_slug"] == "demo"
    assert payload["space_active"] is True
    assert activations == [("demo", "imap-mail", True)]

    saved = json.loads((active_home / "spaces" / "demo" / "mail.json").read_text(encoding="utf-8"))
    inbox = saved["inboxes"][0]
    assert inbox["id"] == "work"
    assert inbox["label"] == "Arbeitsmail"
    assert inbox["imap_host"] == "imap.gmail.com"
    assert inbox["smtp_host"] == "smtp.gmail.com"
    assert inbox["default"] is True


def test_mail_suggest_config_falls_back_to_generic_imap_and_warns():
    from tools import mail_imap

    result = mail_imap.suggest_mail_config("user@example.org", "secret", account_id="work")

    assert result["success"] is True
    assert result["provider"] == "IMAP/SMTP"
    assert result["domain"] == "example.org"
    assert result["warnings"]
    inbox = result["config"]["inboxes"][0]
    assert inbox["imap_host"] == "imap.example.org"
    assert inbox["smtp_host"] == "smtp.example.org"
    assert inbox["confidence"] == "fallback"


def test_mail_imap_prefers_active_profile_home_for_request_scoped_config(monkeypatch, tmp_path):
    import sys

    import_path_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    (active_home / "spaces" / "demo").mkdir(parents=True)
    (active_home / "spaces" / "demo" / "mail.json").write_text(
        json.dumps({"inboxes": [{"id": "active", "imap_host": "imap.active.example"}]}),
        encoding="utf-8",
    )

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("tools.mail_imap", None)
    mail_imap = importlib.import_module("tools.mail_imap")

    monkeypatch.setattr(mail_imap, "get_active_webui_home", lambda: active_home)

    config = mail_imap.get_space_config("demo")

    assert config is not None
    assert config["inboxes"][0]["id"] == "active"
    assert config["inboxes"][0]["imap_host"] == "imap.active.example"


def test_mail_search_prefers_sidekick_workspace_env_when_user_task_missing(monkeypatch):
    from tools import mail_search

    captured = {}

    monkeypatch.delenv("HERMES_WEBUI_ACTIVE_WORKSPACE", raising=False)
    monkeypatch.setenv("SIDEKICK_WEBUI_ACTIVE_WORKSPACE", "demo")

    def fake_get_inbox_config(space_slug, inbox_id):
        captured["space_slug"] = space_slug
        captured["inbox_id"] = inbox_id
        return None

    monkeypatch.setattr(mail_search, "get_inbox_config", fake_get_inbox_config)

    result = json.loads(mail_search._handler({"inbox_id": "work", "query": "test"}))

    assert captured["space_slug"] == "demo"
    assert captured["inbox_id"] == "work"
    assert result["error"] == "Inbox not found"


def test_mail_search_prefers_hermes_workspace_env_when_user_task_missing(monkeypatch):
    from tools import mail_search

    captured = {}

    monkeypatch.delenv("SIDEKICK_WEBUI_ACTIVE_WORKSPACE", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_ACTIVE_WORKSPACE", "legacy-demo")

    def fake_get_inbox_config(space_slug, inbox_id):
        captured["space_slug"] = space_slug
        captured["inbox_id"] = inbox_id
        return None

    monkeypatch.setattr(mail_search, "get_inbox_config", fake_get_inbox_config)

    result = json.loads(mail_search._handler({"inbox_id": "work", "query": "test"}))

    assert captured["space_slug"] == "legacy-demo"
    assert captured["inbox_id"] == "work"
    assert result["error"] == "Inbox not found"


def test_mail_send_prefers_sidekick_workspace_env_when_user_task_missing(monkeypatch):
    from tools import mail_send

    captured = {}

    monkeypatch.delenv("HERMES_WEBUI_ACTIVE_WORKSPACE", raising=False)
    monkeypatch.setenv("SIDEKICK_WEBUI_ACTIVE_WORKSPACE", "demo")

    def fake_get_inbox_config(space_slug, inbox_id):
        captured["space_slug"] = space_slug
        captured["inbox_id"] = inbox_id
        return None

    monkeypatch.setattr(mail_send, "get_inbox_config", fake_get_inbox_config)

    result = json.loads(
        mail_send._handler(
            {
                "inbox_id": "work",
                "to": "user@example.com",
                "subject": "Hi",
                "body": "Hello",
            }
        )
    )

    assert captured["space_slug"] == "demo"
    assert captured["inbox_id"] == "work"
    assert result["error"] == "Inbox not found"


def test_mail_read_prefers_hermes_workspace_env_when_user_task_missing(monkeypatch):
    from tools import mail_read

    captured = {}

    monkeypatch.delenv("SIDEKICK_WEBUI_ACTIVE_WORKSPACE", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_ACTIVE_WORKSPACE", "legacy-demo")

    def fake_get_inbox_config(space_slug, inbox_id):
        captured["space_slug"] = space_slug
        captured["inbox_id"] = inbox_id
        return None

    monkeypatch.setattr(mail_read, "get_inbox_config", fake_get_inbox_config)

    result = json.loads(mail_read._handler({"inbox_id": "work"}))

    assert captured["space_slug"] == "legacy-demo"
    assert captured["inbox_id"] == "work"
    assert result["error"] == "Inbox not found"


def test_mail_folders_prefers_hermes_workspace_env_when_user_task_missing(monkeypatch):
    from tools import mail_folders

    captured = {}

    monkeypatch.delenv("SIDEKICK_WEBUI_ACTIVE_WORKSPACE", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_ACTIVE_WORKSPACE", "legacy-demo")

    def fake_get_space_config(space_slug):
        captured["space_slug"] = space_slug
        return None

    monkeypatch.setattr(mail_folders, "get_space_config", fake_get_space_config)

    result = json.loads(mail_folders._handler({"inbox_id": "work"}))

    assert captured["space_slug"] == "legacy-demo"
    assert result["error"] == "No mail config found for this space"


def test_space_engine_uses_active_profile_home_after_import(monkeypatch, tmp_path):
    import sys

    import_path_home = tmp_path / "import-home"
    active_home_a = tmp_path / "active-home-a"
    active_home_b = tmp_path / "active-home-b"
    (active_home_a / "spaces" / "alpha").mkdir(parents=True)
    (active_home_b / "spaces" / "beta").mkdir(parents=True)

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("web.api.space_engine", None)
    space_engine = importlib.import_module("web.api.space_engine")

    monkeypatch.setattr(space_engine, "get_active_webui_home", lambda: active_home_a)
    monkeypatch.setattr(space_engine, "_seed_default_space_from_consciousness", lambda: None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_ROOTS", None, raising=False)

    assert space_engine.Space("alpha").root == active_home_a / "spaces" / "alpha"
    assert [space.slug for space in space_engine.get_all_spaces()] == ["alpha"]

    monkeypatch.setattr(space_engine, "get_active_webui_home", lambda: active_home_b)
    assert [space.slug for space in space_engine.get_all_spaces()] == ["beta"]


def test_workspace_isolation_uses_active_profile_home_after_import(monkeypatch, tmp_path):
    import sys

    import_path_home = tmp_path / "import-home"
    active_home_a = tmp_path / "active-home-a"
    active_home_b = tmp_path / "active-home-b"
    (active_home_a / "workspaces" / "alpha").mkdir(parents=True)
    (active_home_b / "workspaces" / "beta").mkdir(parents=True)

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("web.api.workspace_isolation", None)
    workspace_isolation = importlib.import_module("web.api.workspace_isolation")

    monkeypatch.setattr(workspace_isolation, "get_active_webui_home", lambda: active_home_a)

    assert workspace_isolation.Workspace("alpha").root == active_home_a / "workspaces" / "alpha"
    assert [ws.slug for ws in workspace_isolation.get_all_workspaces()] == ["alpha"]

    monkeypatch.setattr(workspace_isolation, "get_active_webui_home", lambda: active_home_b)
    assert [ws.slug for ws in workspace_isolation.get_all_workspaces()] == ["beta"]


def test_profile_switch_refreshes_space_roots_after_import(monkeypatch):
    import sys
    import tempfile

    fake_home = Path(tempfile.mkdtemp()) / "fake-home"
    import_home = Path(tempfile.mkdtemp()) / "import-home"
    active_home = Path(tempfile.mkdtemp()) / "active-home"
    fake_home.mkdir(parents=True, exist_ok=True)
    import_home.mkdir(parents=True, exist_ok=True)
    active_home.mkdir(parents=True, exist_ok=True)
    (active_home / "spaces" / "demo").mkdir(parents=True)
    (active_home / "workspaces" / "demo").mkdir(parents=True)

    monkeypatch.setattr(Path, "home", lambda: fake_home)
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_home))

    sys.modules.pop("web.api.workspace", None)
    sys.modules.pop("web.api.space_engine", None)
    sys.modules.pop("web.api.workspace_isolation", None)
    sys.modules.pop("web.api.config", None)
    sys.modules.pop("web.api.profiles", None)

    workspace = importlib.import_module("web.api.workspace")
    space_engine = importlib.import_module("web.api.space_engine")
    workspace_isolation = importlib.import_module("web.api.workspace_isolation")
    profiles = importlib.import_module("web.api.profiles")

    profiles._set_hermes_home(active_home)

    assert space_engine.SPACES_ROOT == active_home / "spaces"
    assert workspace_isolation.WORKSPACES_ROOT == active_home / "workspaces"
    assert workspace.resolve_trusted_workspace(active_home / "spaces" / "demo") == (
        active_home / "spaces" / "demo"
    ).resolve()
    assert workspace_isolation.Workspace("demo").root == active_home / "workspaces" / "demo"


def test_agents_llm_config_uses_active_profile_home_after_import(monkeypatch, tmp_path):
    import sys

    import_path_home = tmp_path / "import-home"
    active_home_a = tmp_path / "active-home-a"
    active_home_b = tmp_path / "active-home-b"
    active_home_a.mkdir(parents=True)
    active_home_b.mkdir(parents=True)
    (active_home_a / ".env").write_text("OPENROUTER_API_KEY=key-a\n", encoding="utf-8")
    (active_home_b / ".env").write_text("OPENROUTER_API_KEY=key-b\n", encoding="utf-8")
    (active_home_a / "config.yaml").write_text("model:\n  default: model-a\n", encoding="utf-8")
    (active_home_b / "config.yaml").write_text("model:\n  default: model-b\n", encoding="utf-8")

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    sys.modules.pop("web.api.agents", None)
    agents = importlib.import_module("web.api.agents")

    monkeypatch.setattr(agents, "get_active_webui_home", lambda: active_home_a)
    monkeypatch.setattr("web.api.config.resolve_active_provider_context", lambda: {})

    cfg_a = agents._load_llm_config()

    monkeypatch.setattr(agents, "get_active_webui_home", lambda: active_home_b)
    cfg_b = agents._load_llm_config()

    assert cfg_a["api_key"] == "key-a"
    assert cfg_a["model"] == "model-a"
    assert cfg_b["api_key"] == "key-b"
    assert cfg_b["model"] == "model-b"


def test_oauth_auth_json_uses_hermes_home_when_sidekick_home_missing(monkeypatch, tmp_path):
    import sys

    active_home = tmp_path / "active-home"
    active_home.mkdir(parents=True)
    (active_home / "auth.json").write_text(
        json.dumps({"credential_pool": {"openai-codex": []}}),
        encoding="utf-8",
    )

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(active_home))

    sys.modules.pop("web.api.oauth", None)
    oauth = importlib.import_module("web.api.oauth")

    assert oauth.AUTH_JSON_PATH == active_home / "auth.json"
    assert oauth.read_auth_json() == {"credential_pool": {"openai-codex": []}}


def test_oauth_read_auth_json_uses_active_profile_after_import(monkeypatch, tmp_path):
    import sys

    import_path_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    import_path_home.mkdir(parents=True)
    active_home.mkdir(parents=True)
    (import_path_home / "auth.json").write_text(
        json.dumps({"credential_pool": {"openai-codex": [{"id": "import"}]}}),
        encoding="utf-8",
    )
    (active_home / "auth.json").write_text(
        json.dumps({"credential_pool": {"openai-codex": [{"id": "active"}]}}),
        encoding="utf-8",
    )

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("web.api.oauth", None)
    oauth = importlib.import_module("web.api.oauth")
    profiles = importlib.import_module("web.api.profiles")

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "coder")
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: active_home)

    assert oauth.read_auth_json() == {"credential_pool": {"openai-codex": [{"id": "active"}]}}


def test_profile_switch_refreshes_state_paths_after_import(monkeypatch, tmp_path):
    import sys

    import_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    (import_home / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (active_home / "auth.json").parent.mkdir(parents=True, exist_ok=True)
    (import_home / "auth.json").write_text(
        json.dumps({"credential_pool": {"openai-codex": [{"id": "import"}]}}),
        encoding="utf-8",
    )
    (active_home / "auth.json").write_text(
        json.dumps({"credential_pool": {"openai-codex": [{"id": "active"}]}}),
        encoding="utf-8",
    )
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_home))

    sys.modules.pop("web.api.auth", None)
    sys.modules.pop("web.api.error_logger", None)
    sys.modules.pop("web.api.appstore", None)
    sys.modules.pop("web.api.agent_workspace", None)
    sys.modules.pop("web.api.oauth", None)
    sys.modules.pop("web.api.config", None)
    auth = importlib.import_module("web.api.auth")
    error_logger = importlib.import_module("web.api.error_logger")
    appstore = importlib.import_module("web.api.appstore")
    agent_workspace = importlib.import_module("web.api.agent_workspace")
    oauth = importlib.import_module("web.api.oauth")
    config = importlib.import_module("web.api.config")
    profiles = importlib.import_module("web.api.profiles")

    profiles._set_hermes_home(active_home)

    token = auth.create_session()
    error_id = error_logger.log_error(message="profile-switch")

    assert token
    assert error_id > 0
    assert config.STATE_DIR == active_home / "state" / "webui"
    assert auth._SESSIONS_FILE == active_home / "state" / "webui" / ".sessions.json"
    assert error_logger.DB_PATH == active_home / "state" / "webui" / "logs" / "errors.db"
    assert appstore._ENV_FILE == active_home / ".env"
    assert appstore._CONFIG_FILE == active_home / "config.yaml"
    assert agent_workspace.HERMES_HOME == active_home
    assert agent_workspace.WORKSPACES_ROOT == active_home / "workspaces"
    assert oauth.AUTH_JSON_PATH == active_home / "auth.json"
    assert oauth.read_auth_json() == {"credential_pool": {"openai-codex": [{"id": "active"}]}}
    assert (active_home / "state" / "webui" / ".sessions.json").exists()
    assert (active_home / "state" / "webui" / "logs" / "errors.db").exists()
    assert (active_home / "auth.json").exists()
    assert not (import_home / "state" / "webui" / ".sessions.json").exists()
    assert not (import_home / "state" / "webui" / "logs" / "errors.db").exists()
    assert json.loads((import_home / "auth.json").read_text(encoding="utf-8")) == {
        "credential_pool": {"openai-codex": [{"id": "import"}]}
    }


def test_session_import_uses_active_default_workspace_and_model(monkeypatch, tmp_path):
    import sys

    import_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    import_workspace = tmp_path / "import-workspace"
    active_workspace = tmp_path / "active-workspace"
    import_workspace.mkdir()
    active_workspace.mkdir()

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_home))
    monkeypatch.setenv("SIDEKICK_WEBUI_DEFAULT_WORKSPACE", str(import_workspace))
    monkeypatch.setenv("SIDEKICK_WEBUI_DEFAULT_MODEL", "import-model")

    sys.modules.pop("web.api.routes", None)
    sys.modules.pop("web.api.config", None)
    sys.modules.pop("web.api.profiles", None)
    routes = importlib.import_module("web.api.routes")
    profiles = importlib.import_module("web.api.profiles")

    profiles._set_hermes_home(active_home)
    monkeypatch.setenv("SIDEKICK_WEBUI_DEFAULT_WORKSPACE", str(active_workspace))
    monkeypatch.setenv("SIDEKICK_WEBUI_DEFAULT_MODEL", "active-model")
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload},
    )

    response = routes._handle_session_import(
        object(),
        {
            "messages": [{"role": "user", "content": "hello"}],
            "title": "Imported session",
        },
    )

    assert response["status"] == 200
    session = response["payload"]["session"]
    assert session["workspace"] == str(active_workspace.resolve())
    assert session["model"] == "active-model"


def test_workspace_module_refreshes_config_paths_after_import(monkeypatch, tmp_path):
    import sys

    import_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    import_ws = tmp_path / "import-workspace"
    active_ws = tmp_path / "active-workspace"
    import_ws.mkdir()
    active_ws.mkdir()

    import_state = import_home / "state" / "webui"
    active_state = active_home / "state" / "webui"
    import_state.mkdir(parents=True)
    active_state.mkdir(parents=True)
    (import_state / "workspaces.json").write_text(
        json.dumps([{"path": str(import_ws), "name": "import"}]),
        encoding="utf-8",
    )
    (active_state / "workspaces.json").write_text(
        json.dumps([{"path": str(active_ws), "name": "active"}]),
        encoding="utf-8",
    )
    (import_state / "last_workspace.txt").write_text(str(import_ws), encoding="utf-8")
    (active_state / "last_workspace.txt").write_text(str(active_ws), encoding="utf-8")

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_home))

    sys.modules.pop("web.api.workspace", None)
    sys.modules.pop("web.api.config", None)
    sys.modules.pop("web.api.profiles", None)
    workspace = importlib.import_module("web.api.workspace")
    profiles = importlib.import_module("web.api.profiles")

    profiles._set_hermes_home(active_home)

    assert workspace.load_workspaces() == [{"path": str(active_ws.resolve()), "name": "active"}]
    assert workspace.get_last_workspace() == str(active_ws.resolve())

    workspace.save_workspaces([{"path": str(active_ws), "name": "saved"}])

    assert json.loads((active_state / "workspaces.json").read_text(encoding="utf-8")) == [
        {"path": str(active_ws), "name": "saved"}
    ]
    assert json.loads((import_state / "workspaces.json").read_text(encoding="utf-8")) == [
        {"path": str(import_ws), "name": "import"}
    ]


def test_mail_imap_uses_hermes_home_when_sidekick_home_is_missing(monkeypatch, tmp_path):
    import sys

    hermes_home = tmp_path / "hermes-home"
    space_root = hermes_home / "spaces" / "demo"
    space_root.mkdir(parents=True)
    (space_root / "mail.json").write_text(
        json.dumps({"inboxes": [{"id": "work", "imap_host": "imap.example.com"}]}),
        encoding="utf-8",
    )

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    sys.modules.pop("tools.mail_imap", None)
    mail_imap = importlib.import_module("tools.mail_imap")

    config = mail_imap.get_space_config("demo")

    assert config is not None
    assert config["inboxes"][0]["id"] == "work"


def test_streaming_thread_env_sets_both_home_vars(monkeypatch, tmp_path):
    import sys

    import_path_home = tmp_path / "import-home"
    profile_home = tmp_path / "profile-home"
    workspace = profile_home / "workspaces" / "demo"
    workspace.mkdir(parents=True)

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("web.api.streaming", None)
    streaming = importlib.import_module("web.api.streaming")

    env = streaming._build_agent_thread_env({}, str(workspace), "session-123", str(profile_home))

    assert env["SIDEKICK_HOME"] == str(profile_home)
    assert env["HERMES_HOME"] == str(profile_home)


def test_supermemory_client_uses_active_profile_after_import(monkeypatch, tmp_path):
    import sys
    import types

    import_path_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"
    import_path_home.mkdir(parents=True)
    active_home.mkdir(parents=True)
    (import_path_home / "supermemory.json").write_text(
        json.dumps({"api_key": "import-key"}),
        encoding="utf-8",
    )
    (active_home / "supermemory.json").write_text(
        json.dumps({"api_key": "active-key"}),
        encoding="utf-8",
    )

    fake_module = types.ModuleType("supermemory")

    class FakeSupermemory:
        def __init__(self, *, api_key, max_retries, timeout):
            self.api_key = api_key
            self.max_retries = max_retries
            self.timeout = timeout

    fake_module.Supermemory = FakeSupermemory
    monkeypatch.setitem(sys.modules, "supermemory", fake_module)

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("web.api.routes", None)
    routes = importlib.import_module("web.api.routes")

    monkeypatch.setattr(routes, "get_active_webui_home", lambda: import_path_home)
    client_a = routes._get_supermemory_client()
    assert client_a is not None
    assert client_a.api_key == "import-key"

    monkeypatch.setattr(routes, "get_active_webui_home", lambda: active_home)
    client_b = routes._get_supermemory_client()

    assert client_b is not None
    assert client_b.api_key == "active-key"
    assert client_b is not client_a


def test_nova_state_snapshot_cache_tracks_active_profile_home(monkeypatch, tmp_path):
    import os
    import sys

    import_path_home = tmp_path / "import-home"
    active_home_a = tmp_path / "active-home-a"
    active_home_b = tmp_path / "active-home-b"
    import_snapshot = import_path_home / "spaces" / "nova" / "state_snapshot.py"
    snapshot_a = active_home_a / "spaces" / "nova" / "state_snapshot.py"
    snapshot_b = active_home_b / "spaces" / "nova" / "state_snapshot.py"
    import_snapshot.parent.mkdir(parents=True)
    snapshot_a.parent.mkdir(parents=True)
    snapshot_b.parent.mkdir(parents=True)
    import_snapshot.write_text(
        'HOME_MARKER = "import"\n'
        'def load_router_health():\n'
        '    return {"marker": HOME_MARKER}\n',
        encoding="utf-8",
    )
    snapshot_a.write_text(
        'HOME_MARKER = "a"\n'
        'def load_router_health():\n'
        '    return {"marker": HOME_MARKER}\n',
        encoding="utf-8",
    )
    snapshot_b.write_text(
        'HOME_MARKER = "b"\n'
        'def load_router_health():\n'
        '    return {"marker": HOME_MARKER}\n',
        encoding="utf-8",
    )
    fixed_mtime = 1_700_000_000
    os.utime(import_snapshot, (fixed_mtime, fixed_mtime))
    os.utime(snapshot_a, (fixed_mtime, fixed_mtime))
    os.utime(snapshot_b, (fixed_mtime, fixed_mtime))

    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(import_path_home))

    sys.modules.pop("web.api.routes", None)
    sys.modules.pop("web.api.providers", None)
    sys.modules.pop("web.api.nova_paths", None)
    routes = importlib.import_module("web.api.routes")
    providers = importlib.import_module("web.api.providers")
    nova_paths = importlib.import_module("web.api.nova_paths")

    routes._NOVA_ROUTE_STATUS_CACHE.update({"module": None, "path": None, "mtime": None})
    providers._NOVA_STATE_SNAPSHOT_CACHE.update({"module": None, "path": None, "mtime": None})

    monkeypatch.setattr(nova_paths, "get_active_webui_home", lambda: active_home_a)
    status_a = routes._load_nova_route_status()
    module_a = providers._load_nova_state_snapshot_module()

    monkeypatch.setattr(nova_paths, "get_active_webui_home", lambda: active_home_b)
    status_b = routes._load_nova_route_status()
    module_b = providers._load_nova_state_snapshot_module()

    assert status_a["marker"] == "a"
    assert status_b["marker"] == "b"
    assert module_a is not None and getattr(module_a, "HOME_MARKER", None) == "a"
    assert module_b is not None and getattr(module_b, "HOME_MARKER", None) == "b"


def test_streaming_home_restore_uses_both_variables(monkeypatch):
    from web.api import streaming

    monkeypatch.setenv("SIDEKICK_HOME", "mutated-sidekick")
    monkeypatch.setenv("HERMES_HOME", "mutated-hermes")

    streaming._restore_streaming_home_env("original-sidekick", "original-hermes")

    assert streaming.os.environ["SIDEKICK_HOME"] == "original-sidekick"
    assert streaming.os.environ["HERMES_HOME"] == "original-hermes"
