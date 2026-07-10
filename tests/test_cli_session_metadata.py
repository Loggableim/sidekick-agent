from __future__ import annotations

import sqlite3

from web.api import profiles as profiles_mod
from web.api import models as models_mod
from web.api import routes


def test_lookup_cli_session_metadata_uses_direct_state_db_row(tmp_path, monkeypatch):
    prev_default_home = profiles_mod._DEFAULT_SIDEKICK_HOME
    prev_active_profile = profiles_mod._active_profile
    prev_tls_profile = getattr(profiles_mod._tls, "profile", None)
    try:
        with monkeypatch.context() as mp:
            mp.setenv("SIDEKICK_BASE_HOME", str(tmp_path))
            profiles_mod.refresh_profile_base_home_from_env()
            profiles_mod.clear_request_profile()

            db_path = tmp_path / "state.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    source TEXT,
                    model TEXT,
                    started_at REAL,
                    message_count INTEGER
                );

                CREATE TABLE messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    timestamp REAL
                );
                """
            )
            conn.execute(
                """
                INSERT INTO sessions (id, title, source, model, started_at, message_count)
                VALUES ('sess-1', 'Custom Title', 'cli', 'deepseek-v4-flash', 1000, 2)
                """
            )
            conn.executemany(
                """
                INSERT INTO messages (session_id, role, content, timestamp)
                VALUES ('sess-1', ?, ?, ?)
                """,
                [
                    ("user", "hello", 2000),
                    ("assistant", "hi", 2005),
                ],
            )
            conn.commit()
            conn.close()

            monkeypatch.setattr(
                routes,
                "get_cli_sessions",
                lambda: (_ for _ in ()).throw(AssertionError("full CLI scan should not run")),
            )

            meta = routes._lookup_cli_session_metadata("sess-1")
            assert meta["session_id"] == "sess-1"
            assert meta["title"] == "Custom Title"
            assert meta["source"] == "cli"
            assert meta["actual_message_count"] == 2
            assert meta["actual_user_message_count"] == 1
            assert meta["updated_at"] == 2005
    finally:
        profiles_mod._DEFAULT_SIDEKICK_HOME = prev_default_home
        profiles_mod._active_profile = prev_active_profile
        profiles_mod._tls.profile = prev_tls_profile
        profiles_mod._invalidate_root_profile_cache()


def test_get_cli_sessions_uses_index_title_without_loading_session_metadata(tmp_path, monkeypatch):
    prev_default_home = profiles_mod._DEFAULT_SIDEKICK_HOME
    prev_active_profile = profiles_mod._active_profile
    prev_tls_profile = getattr(profiles_mod._tls, "profile", None)
    try:
        with monkeypatch.context() as mp:
            mp.setenv("SIDEKICK_BASE_HOME", str(tmp_path))
            profiles_mod.refresh_profile_base_home_from_env()
            profiles_mod.clear_request_profile()

            home = tmp_path / "home"
            home.mkdir(parents=True)
            (home / "state.db").write_bytes(b"")

            mp.setattr(profiles_mod, "get_active_profile_home", lambda: str(home))
            mp.setattr(profiles_mod, "get_active_profile_name", lambda: "default")
            mp.setattr(models_mod, "get_last_workspace", lambda: str(tmp_path / "workspace"))
            mp.setattr(models_mod, "get_claude_code_sessions", lambda: [])
            mp.setattr(
                models_mod,
                "read_importable_agent_session_rows",
                lambda *args, **kwargs: [
                    {
                        "id": "sess-1",
                        "title": "",
                        "source": "cli",
                        "model": "gpt",
                        "message_count": 2,
                        "actual_message_count": 2,
                        "actual_user_message_count": 1,
                        "started_at": 1000.0,
                        "last_activity": 1005.0,
                        "raw_source": "cli",
                        "session_source": "cli",
                        "source_label": "CLI",
                    }
                ],
            )
            mp.setattr(models_mod, "_lookup_index_title", lambda sid: "Renamed Title" if sid == "sess-1" else None)
            mp.setattr(models_mod.Session, "load_metadata_only", classmethod(lambda cls, sid: (_ for _ in ()).throw(AssertionError("metadata load should not run"))))
            mp.setattr(models_mod, "get_cli_session_messages", lambda sid: (_ for _ in ()).throw(AssertionError("full message load should not run")))

            sessions = models_mod.get_cli_sessions()

            assert len(sessions) == 1
            assert sessions[0]["session_id"] == "sess-1"
            assert sessions[0]["title"] == "Renamed Title"
    finally:
        profiles_mod._DEFAULT_SIDEKICK_HOME = prev_default_home
        profiles_mod._active_profile = prev_active_profile
        profiles_mod._tls.profile = prev_tls_profile
        profiles_mod._invalidate_root_profile_cache()


def test_web_server_session_search_uses_compat_sessiondb(tmp_path, monkeypatch):
    prev_default_home = profiles_mod._DEFAULT_SIDEKICK_HOME
    prev_active_profile = profiles_mod._active_profile
    prev_tls_profile = getattr(profiles_mod._tls, "profile", None)
    try:
        with monkeypatch.context() as mp:
            mp.setenv("SIDEKICK_BASE_HOME", str(tmp_path))
            mp.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
            profiles_mod.refresh_profile_base_home_from_env()
            profiles_mod.clear_request_profile()

            from cli import web_server
            from fastapi.testclient import TestClient
            from runtime._compat.shim_state import SessionDB

            db = SessionDB(tmp_path / "home" / "state.db")
            db.create_session("sess-1", title="Hello session", source="cli", model="gpt")
            db.append_message("sess-1", "user", "hello world")
            db.append_message("sess-1", "assistant", "response")
            db.create_session("sess-2", title="Other session", source="cli", model="gpt")
            db.append_message("sess-2", "user", "something else")
            db.close()

            client = TestClient(web_server.app)
            response = client.get(
                "/api/sessions/search?q=hello",
                headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
            )

            assert response.status_code == 200
            payload = response.json()
            assert payload["count"] >= 1
            assert payload["results"][0]["session_id"] == "sess-1"
            assert payload["results"][0]["match_type"] == "content"
    finally:
        profiles_mod._DEFAULT_SIDEKICK_HOME = prev_default_home
        profiles_mod._active_profile = prev_active_profile
        profiles_mod._tls.profile = prev_tls_profile
        profiles_mod._invalidate_root_profile_cache()
