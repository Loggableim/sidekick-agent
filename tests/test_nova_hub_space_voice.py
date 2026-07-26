from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import os
import pytest

_COCKPIT_ROOT = os.getenv("SIDEKICK_COCKPIT_ROOT", "").strip()
pytestmark = pytest.mark.skipif(
    not _COCKPIT_ROOT,
    reason="external cockpit integration requires SIDEKICK_COCKPIT_ROOT",
)


MODULE_PATH = Path("C:/SidekickPortable/home/cockpit/dashboard_server.py")
COCKPIT_DIR = MODULE_PATH.parent


def load_dashboard_module():
    if str(COCKPIT_DIR) not in sys.path:
        sys.path.insert(0, str(COCKPIT_DIR))
    spec = importlib.util.spec_from_file_location("nova_dashboard_under_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def create_kanban_db(path: Path, rows: list[tuple[str, str, str, int]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL, priority INTEGER DEFAULT 0, assignee TEXT, body TEXT)"
    )
    con.executemany(
        "INSERT INTO tasks (id, title, status, priority, assignee, body) VALUES (?, ?, ?, ?, '', '')",
        rows,
    )
    con.commit()
    con.close()


def test_space_voice_detects_spoken_slug_variants(tmp_path):
    dashboard = load_dashboard_module()
    dashboard.SIDEKICK_SPACES_DIR = tmp_path / "spaces"
    (dashboard.SIDEKICK_SPACES_DIR / "tirol-tourismus").mkdir(parents=True)

    assert dashboard._detect_space_slug("im space tirol tourismus offene aufgaben") == "tirol-tourismus"


def test_space_voice_answers_open_kanban_tasks(tmp_path):
    dashboard = load_dashboard_module()
    dashboard.SIDEKICK_SPACES_DIR = tmp_path / "spaces"
    dashboard.SIDEKICK_WEBUI_DIR = tmp_path / "webui"
    space = dashboard.SIDEKICK_SPACES_DIR / "tirol-tourismus"
    create_kanban_db(
        space / "kanban" / "boards" / "main" / "kanban.db",
        [
            ("a", "Landingpage pruefen", "todo", 1),
            ("b", "Deployment abschliessen", "done", 1),
        ],
    )

    answer = dashboard._space_voice_answer("hey nova sind im space tirol tourismus noch aufgaben offen")

    assert answer is not None
    assert "1 von 2" in answer
    assert "Landingpage pruefen" in answer


def test_space_voice_reports_image_queue_without_eta(tmp_path):
    dashboard = load_dashboard_module()
    dashboard.SIDEKICK_SPACES_DIR = tmp_path / "spaces"
    dashboard.SIDEKICK_WEBUI_DIR = tmp_path / "webui"
    session_dir = dashboard.SIDEKICK_SPACES_DIR / "hostazar" / "sessions"
    session_dir.mkdir(parents=True)
    (session_dir / "_index.json").write_text(
        json.dumps(
            [
                {
                    "session_id": "s1",
                    "title": "Bildgenerierung Queue",
                    "workspace_slug": "hostazar",
                    "active_stream_id": "stream1",
                }
            ]
        ),
        encoding="utf-8",
    )

    answer = dashboard._space_voice_answer("hey nova wie lange dauern die bildgenerierungen im hostazar space noch")

    assert answer is not None
    assert "hostazar" in answer
    assert "aktive" in answer
    assert "Restzeit" in answer


def test_space_voice_lists_available_spaces_without_llm_fallback(tmp_path):
    dashboard = load_dashboard_module()
    dashboard.SIDEKICK_SPACES_DIR = tmp_path / "spaces"
    dashboard.SIDEKICK_WEBUI_DIR = tmp_path / "webui"
    for slug in ["nova", "hostazar", "tirol-tourismus"]:
        (dashboard.SIDEKICK_SPACES_DIR / slug).mkdir(parents=True)

    answer = dashboard._space_voice_answer("hey nova hast du zugriff auf die liste aller spaces in der webui")

    assert answer is not None
    assert "Ich kann die lokalen WebUI-Spaces recherchieren" in answer
    assert "3 Spaces" in answer
    assert "hostazar" in answer
    assert "tirol-tourismus" in answer


def test_space_voice_searches_open_tasks_across_all_spaces(tmp_path):
    dashboard = load_dashboard_module()
    dashboard.SIDEKICK_SPACES_DIR = tmp_path / "spaces"
    dashboard.SIDEKICK_WEBUI_DIR = tmp_path / "webui"
    create_kanban_db(
        dashboard.SIDEKICK_SPACES_DIR / "hostazar" / "kanban" / "boards" / "main" / "kanban.db",
        [("a", "Queue pruefen", "todo", 1)],
    )
    create_kanban_db(
        dashboard.SIDEKICK_SPACES_DIR / "tirol-tourismus" / "kanban" / "boards" / "main" / "kanban.db",
        [("b", "Artikel live stellen", "done", 1)],
    )

    answer = dashboard._space_voice_answer("hey nova recherchiere in den webui spaces ob aufgaben offen sind")

    assert answer is not None
    assert "Offene Aufgaben" in answer
    assert "hostazar" in answer
    assert "Queue pruefen" in answer


def test_voice_llm_prioritizes_fast_ollama_cloud_model(tmp_path, monkeypatch):
    dashboard = load_dashboard_module()
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    dashboard.SIDEKICK_AUTH_PATH = tmp_path / "auth.json"
    dashboard.SIDEKICK_AUTH_PATH.write_text(
        json.dumps(
            {
                "providers": {
                    "ollama-cloud": [
                        {
                            "access_token": "test-token",
                            "base_url": "https://ollama.com/v1",
                        }
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "_candidate_auth_paths", lambda: [dashboard.SIDEKICK_AUTH_PATH])
    monkeypatch.setattr(dashboard, "_candidate_env_paths", lambda: [tmp_path / "missing.env"])

    attempts = dashboard._voice_llm_attempts()

    assert attempts[0]["provider"] == "ollama-cloud"
    assert attempts[0]["model"] == "gemma3:4b"
    assert attempts[0]["url"] == "https://ollama.com/v1/chat/completions"
    assert attempts[0]["token"] == "test-token"
    assert attempts[1]["model"] == "gemma4:31b"


def test_voice_llm_keeps_local_fallback_without_ollama_key(tmp_path, monkeypatch):
    dashboard = load_dashboard_module()
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    dashboard.SIDEKICK_AUTH_PATH = tmp_path / "missing-auth.json"
    dashboard.SIDEKICK_HOME = tmp_path / "missing-home"
    monkeypatch.setattr(dashboard, "_candidate_auth_paths", lambda: [dashboard.SIDEKICK_AUTH_PATH])
    monkeypatch.setattr(dashboard, "_candidate_env_paths", lambda: [dashboard.SIDEKICK_HOME / ".env"])

    attempts = dashboard._voice_llm_attempts()

    assert attempts[0]["provider"] == "local-8081"


def test_voice_llm_reads_ollama_key_from_dotenv(tmp_path, monkeypatch):
    dashboard = load_dashboard_module()
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    dashboard.SIDEKICK_AUTH_PATH = tmp_path / "missing-auth.json"
    dashboard.SIDEKICK_HOME = tmp_path
    (tmp_path / ".env").write_text("OLLAMA_API_KEY=test-dotenv-token\n", encoding="utf-8")
    monkeypatch.setattr(dashboard, "_candidate_auth_paths", lambda: [dashboard.SIDEKICK_AUTH_PATH])
    monkeypatch.setattr(dashboard, "_candidate_env_paths", lambda: [tmp_path / ".env"])

    attempts = dashboard._voice_llm_attempts()

    assert attempts[0]["provider"] == "ollama-cloud"
    assert attempts[0]["model"] == "gemma3:4b"
    assert attempts[0]["token"] == "test-dotenv-token"


def test_voice_llm_reads_ollama_from_alternate_auth_path(tmp_path, monkeypatch):
    dashboard = load_dashboard_module()
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    dashboard.SIDEKICK_HOME = tmp_path / "home"
    dashboard.SIDEKICK_AUTH_PATH = tmp_path / "missing-sidekick-auth.json"
    dashboard.AUTH_PATH = tmp_path / "sidekick-auth.json"
    dashboard.AUTH_PATH.write_text(
        json.dumps({"providers": {"ollama-cloud": {"access_token": "test-alt-auth-token"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "_candidate_auth_paths", lambda: [dashboard.SIDEKICK_AUTH_PATH, dashboard.AUTH_PATH])
    monkeypatch.setattr(dashboard, "_candidate_env_paths", lambda: [tmp_path / "missing.env"])

    attempts = dashboard._voice_llm_attempts()

    assert attempts[0]["provider"] == "ollama-cloud"
    assert attempts[0]["token"] == "test-alt-auth-token"


def test_voice_llm_adds_opencode_go_after_working_ollama(tmp_path, monkeypatch):
    dashboard = load_dashboard_module()
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_GO_API_KEY", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OPENCODE_GO_BASE_URL", raising=False)
    dashboard.SIDEKICK_AUTH_PATH = tmp_path / "auth.json"
    dashboard.SIDEKICK_AUTH_PATH.write_text(
        json.dumps(
            {
                "providers": {
                    "ollama-cloud": [{"access_token": "ollama-token"}],
                    "opencode-go": [{"access_token": "opencode-token", "base_url": "https://opencode.ai/zen/go/v1"}],
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(dashboard, "_candidate_auth_paths", lambda: [dashboard.SIDEKICK_AUTH_PATH])
    monkeypatch.setattr(dashboard, "_candidate_env_paths", lambda: [tmp_path / "missing.env"])

    attempts = dashboard._voice_llm_attempts()

    assert [a["provider"] for a in attempts[:3]] == ["ollama-cloud", "ollama-cloud", "opencode-go"]
    assert attempts[2]["model"] == "deepseek-v4-flash"
