from __future__ import annotations

import json
import logging
import threading
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from sidekick_constants import (
    display_sidekick_home,
    get_config_path,
    get_env_path,
    get_optional_skills_dir,
    get_subprocess_home,
)
from shared.config import ensure_sidekick_home, get_env_value, load_config, parse_env_file, read_raw_config
from shared.config import get_config_value, save_config, set_config_value
from shared.agent_bridge import run_assistant_once
from shared.logging_setup import get_logs_dir, setup_logging
from shared.paths import build_runtime_snapshot, runtime_warnings, sidekick_home, state_dir
from shared.runtime import build_web_runtime, discover_agent_dir, web_state_dir
from shared.sessions import (
    append_message,
    delete_session,
    list_sessions,
    load_session,
    new_session,
    sessions_dir,
    session_status,
    update_session,
)
from web.server import create_server


def test_sidekick_home_defaults_to_user_profile(monkeypatch):
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)
    assert sidekick_home() == (Path.home() / ".sidekick").resolve()


def test_sidekick_home_prefers_sidekick_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "sidekick-home"))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "legacy-home"))
    assert sidekick_home() == (tmp_path / "sidekick-home").resolve()


def test_state_dir_uses_legacy_alias_when_canonical_missing(monkeypatch, tmp_path):
    monkeypatch.delenv("SIDEKICK_STATE_DIR", raising=False)
    monkeypatch.setenv("HERMES_STATE_DIR", str(tmp_path / "legacy-state"))
    assert state_dir() == (tmp_path / "legacy-state").resolve()


def test_runtime_snapshot_reports_legacy_usage(monkeypatch, tmp_path):
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "legacy-home"))
    snapshot = build_runtime_snapshot()
    assert snapshot["legacy_env_detected"] is True


def test_runtime_warnings_flag_repo_local_home(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setenv("SIDEKICK_HOME", str(repo_root / "home"))
    warnings = runtime_warnings(repo_root)
    assert any("inside the repo workspace" in warning for warning in warnings)


def test_web_state_dir_defaults_under_shared_state(monkeypatch, tmp_path):
    monkeypatch.delenv("SIDEKICK_STATE_DIR", raising=False)
    monkeypatch.delenv("HERMES_STATE_DIR", raising=False)
    monkeypatch.delenv("SIDEKICK_WEBUI_STATE_DIR", raising=False)
    monkeypatch.delenv("HERMES_WEBUI_STATE_DIR", raising=False)
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    assert web_state_dir() == (tmp_path / "home" / "state" / "webui").resolve()


def test_discover_agent_dir_prefers_explicit_env(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    agent_dir = tmp_path / "agent"
    agent_dir.mkdir()
    (agent_dir / "run_agent.py").write_text("print('ok')", encoding="utf-8")
    monkeypatch.setenv("SIDEKICK_WEBUI_AGENT_DIR", str(agent_dir))
    assert discover_agent_dir(repo_root) == agent_dir.resolve()


def test_build_web_runtime_picks_explicit_host_port(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SIDEKICK_WEBUI_HOST", "0.0.0.0")
    monkeypatch.setenv("SIDEKICK_WEBUI_PORT", "9999")
    runtime = build_web_runtime(repo_root)
    assert runtime.host == "0.0.0.0"
    assert runtime.port == 9999


def test_discover_python_accepts_legacy_env_var(monkeypatch):
    monkeypatch.delenv("SIDEKICK_WEBUI_PYTHON", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_PYTHON", r"C:\legacy\python.exe")
    from web.api import config as web_config

    assert web_config._discover_python(None) == r"C:\legacy\python.exe"


def test_test_network_block_accepts_legacy_env_var(monkeypatch):
    monkeypatch.delenv("SIDEKICK_WEBUI_TEST_NETWORK_BLOCK", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_TEST_NETWORK_BLOCK", "1")
    import web.server as web_server

    assert web_server._test_network_block_enabled() is True


def test_aiagent_import_error_detail_accepts_legacy_agent_dir(monkeypatch):
    monkeypatch.delenv("SIDEKICK_WEBUI_AGENT_DIR", raising=False)
    monkeypatch.setenv("HERMES_WEBUI_AGENT_DIR", r"C:\legacy\sidekick-agent")
    import web.api.streaming as streaming

    detail = streaming._aiagent_import_error_detail()
    assert "C:\\legacy\\sidekick-agent" in detail
    assert "SIDEKICK_WEBUI_AGENT_DIR" in detail


def test_constants_paths_follow_sidekick_home(monkeypatch, tmp_path):
    home = tmp_path / "sidekick-home"
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    assert get_config_path() == home / "config.yaml"
    assert get_env_path() == home / ".env"


def test_display_sidekick_home_uses_tilde_for_home_relative(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / ".sidekick" / "profiles" / "coder"))
    assert display_sidekick_home() == "~/.sidekick/profiles/coder"


def test_get_optional_skills_dir_prefers_sidekick_override(monkeypatch, tmp_path):
    override = tmp_path / "optional"
    monkeypatch.setenv("SIDEKICK_OPTIONAL_SKILLS", str(override))
    assert get_optional_skills_dir() == override


def test_get_subprocess_home_returns_profile_home_when_present(monkeypatch, tmp_path):
    home = tmp_path / "profile-home"
    profile_home = home / "home"
    profile_home.mkdir(parents=True)
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    assert get_subprocess_home() == str(profile_home)


def test_ensure_sidekick_home_creates_expected_dirs(monkeypatch, tmp_path):
    home = tmp_path / "sidekick-home"
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    ensure_sidekick_home()
    assert (home / "logs").is_dir()
    assert (home / "skills").is_dir()
    assert (home / "state").is_dir()


def test_read_raw_config_returns_empty_when_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    assert read_raw_config() == {}


def test_load_config_deep_merges_user_config(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    (home / "config.yaml").write_text(
        "app:\n  assistant_name: Iris\nwebui:\n  port: 9999\n",
        encoding="utf-8",
    )
    config = load_config()
    assert config["app"]["name"] == "Sidekick"
    assert config["app"]["assistant_name"] == "Iris"
    assert config["webui"]["port"] == 9999


def test_parse_env_file_reads_basic_pairs(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    (home / ".env").write_text(
        "# comment\nOPENAI_API_KEY=abc123\nSIDEKICK_MODE=\"local\"\n",
        encoding="utf-8",
    )
    env = parse_env_file()
    assert env["OPENAI_API_KEY"] == "abc123"
    assert env["SIDEKICK_MODE"] == "local"


def test_get_env_value_prefers_process_env(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    (home / ".env").write_text("OPENAI_API_KEY=file-value\n", encoding="utf-8")
    monkeypatch.setenv("OPENAI_API_KEY", "process-value")
    assert get_env_value("OPENAI_API_KEY") == "process-value"


def test_save_config_writes_yaml(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    path = save_config({"app": {"assistant_name": "Nova"}})
    assert path == home / "config.yaml"
    assert path.exists()


def test_set_config_value_updates_dotted_key(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    path, value = set_config_value("webui.port", "9999")
    assert path == home / "config.yaml"
    assert value == 9999
    assert get_config_value("webui.port") == 9999


def test_get_config_value_returns_default_for_missing_key(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    assert get_config_value("missing.path", "fallback") == "fallback"


def test_get_logs_dir_uses_sidekick_home(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    assert get_logs_dir() == home / "logs"


def test_setup_logging_creates_log_files(monkeypatch, tmp_path):
    home = tmp_path / "home"
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    logs_dir = setup_logging(force=True)
    logger = logging.getLogger("sidekick.test")
    logger.warning("test warning")
    assert (logs_dir / "agent.log").exists()
    assert (logs_dir / "errors.log").exists()


def test_setup_logging_ignores_locked_agent_log_rollover(monkeypatch, tmp_path):
    import logging.handlers

    home = tmp_path / "home"
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    setup_logging(force=True)

    agent_handler = next(
        handler
        for handler in logging.getLogger().handlers
        if getattr(handler, "_sidekick_managed", False)
        and Path(getattr(handler, "baseFilename", "")).name == "agent.log"
    )
    agent_handler.maxBytes = 1
    agent_handler.backupCount = 1

    logging.getLogger("sidekick.test").warning("seed rollover file")

    handled_errors = []
    monkeypatch.setattr(agent_handler, "handleError", lambda record: handled_errors.append(record))

    real_rename = logging.handlers.os.rename

    def locked_rename(source, dest):
        if Path(source).name == "agent.log" and Path(dest).name == "agent.log.1":
            raise PermissionError(32, "locked log", str(source), str(dest))
        return real_rename(source, dest)

    monkeypatch.setattr(logging.handlers.os, "rename", locked_rename)

    logging.getLogger("sidekick.test").warning("trigger locked rollover")

    assert handled_errors == []


def test_run_assistant_once_returns_fallback_when_command_missing(monkeypatch):
    monkeypatch.setattr("shared.agent_bridge._detect_legacy_sidekick", lambda: None)
    result = run_assistant_once("Hello")
    assert result.ok is False
    assert result.backend == "none"
    assert "not found" in (result.error or "")


def test_run_assistant_once_returns_stdout_when_legacy_command_succeeds(monkeypatch):
    monkeypatch.setattr("shared.agent_bridge._detect_legacy_sidekick", lambda: "sidekick")
    monkeypatch.setattr(
        "shared.agent_bridge.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Bridge reply\n", stderr=""),
    )
    result = run_assistant_once("Hello")
    assert result.ok is True
    assert result.reply == "Bridge reply"


def test_run_assistant_once_returns_fallback_for_empty_stdout(monkeypatch):
    monkeypatch.setattr("shared.agent_bridge._detect_legacy_sidekick", lambda: "sidekick")
    monkeypatch.setattr(
        "shared.agent_bridge.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )
    result = run_assistant_once("Hello")
    assert result.ok is False
    assert result.error == "empty response"


def test_create_server_uses_runtime_host_and_port(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SIDEKICK_WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("SIDEKICK_WEBUI_PORT", "0")
    server = create_server()
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_web_server_health_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SIDEKICK_WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("SIDEKICK_WEBUI_PORT", "0")
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=5) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        assert data["status"] == "ok"
        assert "sessions" in data
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_web_server_agents_list_endpoint_initializes_cleanly(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SIDEKICK_WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("SIDEKICK_WEBUI_PORT", "0")
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(f"http://{host}:{port}/api/agents/list", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert isinstance(payload, dict)
        assert payload.get("agents") is not None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_web_helpers_ignore_client_disconnects():
    from web.api.helpers import j, t

    class _BrokenWriter:
        def write(self, body):
            raise BrokenPipeError()

    handler = SimpleNamespace(
        headers={},
        wfile=_BrokenWriter(),
        send_response=lambda *args, **kwargs: None,
        send_header=lambda *args, **kwargs: None,
        end_headers=lambda: None,
    )

    j(handler, {"ok": True})
    t(handler, "ok")


def test_new_session_persists_to_web_state(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    session = new_session(title="Test Session", model="gpt-test")
    assert (sessions_dir() / f"{session.session_id}.json").exists()
    loaded = load_session(session.session_id)
    assert loaded is not None
    assert loaded.title == "Test Session"


def test_list_sessions_returns_compact_rows(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    new_session(title="One")
    monkeypatch.setattr(list_sessions, "_migrated", True, raising=False)
    rows = list_sessions()
    assert len(rows) == 1
    assert rows[0]["title"] == "One"
    assert "message_count" in rows[0]


def test_update_and_delete_session(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    session = new_session(title="Before")
    updated = update_session(session.session_id, title="After", model="gpt-next")
    assert updated is not None
    assert updated.title == "After"
    assert updated.model == "gpt-next"
    assert delete_session(session.session_id) is True
    assert load_session(session.session_id) is None


def test_append_message_updates_session(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    session = new_session()
    updated = append_message(session.session_id, role="user", content="Hello from web")
    assert updated is not None
    assert updated.messages[-1]["content"] == "Hello from web"
    assert updated.title == "Hello from web"


def test_shared_sessions_preserve_webui_only_metadata(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    sess_dir = sessions_dir()
    session_path = sess_dir / "rich-session.json"
    payload = {
        "session_id": "rich-session",
        "title": "Rich Session",
        "workspace": str(tmp_path / "workspace"),
        "model": "gpt-test",
        "messages": [{"role": "user", "content": "hello"}],
        "created_at": 1.0,
        "updated_at": 2.0,
        "workspace_slug": "nova",
        "agent_slug": "coding-agent",
        "worktree_path": str(tmp_path / "worktree"),
        "pending_user_message": "draft me",
        "compression_anchor_summary": "summary",
        "custom_note": "keep me",
    }
    session_path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = load_session("rich-session")
    assert loaded is not None
    assert getattr(loaded, "_extra")["workspace_slug"] == "nova"
    assert getattr(loaded, "_extra")["agent_slug"] == "coding-agent"
    assert getattr(loaded, "_extra")["custom_note"] == "keep me"

    monkeypatch.setattr(list_sessions, "_migrated", True, raising=False)
    rows = list_sessions()
    assert len(rows) == 1
    assert rows[0]["session_id"] == "rich-session"
    assert rows[0]["title"] == "Rich Session"
    assert rows[0]["workspace_slug"] == "nova"
    assert rows[0]["agent_slug"] == "coding-agent"
    assert rows[0]["custom_note"] == "keep me"

    status = session_status("rich-session")
    assert status["workspace"] == str(tmp_path / "workspace")
    assert status["title"] == "Rich Session"
    assert status["workspace_slug"] == "nova"
    assert status["agent_slug"] == "coding-agent"
    assert status["custom_note"] == "keep me"

    updated = update_session("rich-session", title="Updated Rich Session")
    assert updated is not None
    saved = json.loads(session_path.read_text(encoding="utf-8"))
    assert saved["title"] == "Updated Rich Session"
    assert saved["workspace_slug"] == "nova"
    assert saved["agent_slug"] == "coding-agent"
    assert saved["custom_note"] == "keep me"


def test_web_server_session_endpoints(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SIDEKICK_WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("SIDEKICK_WEBUI_PORT", "0")
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        req = urllib.request.Request(
            f"http://{host}:{port}/api/session/new",
            data=json.dumps({"title": "API Session"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            created = json.loads(response.read().decode("utf-8"))
        assert "session" in created
        session_id = created["session"]["session_id"]

        with urllib.request.urlopen(f"http://{host}:{port}/api/sessions", timeout=5) as response:
            listed = json.loads(response.read().decode("utf-8"))
        assert isinstance(listed, dict)
        assert "sessions" in listed

        with urllib.request.urlopen(f"http://{host}:{port}/api/session?session_id={session_id}", timeout=5) as response:
            fetched = json.loads(response.read().decode("utf-8"))
        assert "session" in fetched
        assert fetched["session"]["session_id"] == session_id

        patch_req = urllib.request.Request(
            f"http://{host}:{port}/api/session/rename",
            data=json.dumps({"session_id": session_id, "title": "Renamed Session"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(patch_req, timeout=5) as response:
            patched = json.loads(response.read().decode("utf-8"))
        assert patched["session"]["title"] == "Renamed Session"

        delete_req = urllib.request.Request(
            f"http://{host}:{port}/api/session/delete",
            data=json.dumps({"session_id": session_id}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(delete_req, timeout=5) as response:
            deleted = json.loads(response.read().decode("utf-8"))
        assert deleted["ok"] is True
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_web_server_chat_endpoint_appends_assistant_reply(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SIDEKICK_WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("SIDEKICK_WEBUI_PORT", "0")
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        create_req = urllib.request.Request(
            f"http://{host}:{port}/api/session/new",
            data=json.dumps({"title": "Chat Session"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(create_req, timeout=5) as response:
            created = json.loads(response.read().decode("utf-8"))
        session_id = created["session"]["session_id"]

        update_req = urllib.request.Request(
            f"http://{host}:{port}/api/session/update",
            data=json.dumps(
                {
                    "session_id": session_id,
                    "workspace": str(workspace_dir),
                    "model": "gpt-test",
                    "model_provider": "test-provider",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(update_req, timeout=5) as response:
            updated = json.loads(response.read().decode("utf-8"))
        assert updated["session"]["model"] == "gpt-test"
        assert updated["session"]["model_provider"] == "test-provider"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_web_server_root_serves_html(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SIDEKICK_WEBUI_HOST", "127.0.0.1")
    monkeypatch.setenv("SIDEKICK_WEBUI_PORT", "0")
    server = create_server()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[:2]
        with urllib.request.urlopen(f"http://{host}:{port}/", timeout=5) as response:
            html = response.read().decode("utf-8")
        assert "<title>Sidekick</title>" in html
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
