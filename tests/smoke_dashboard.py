#!/usr/bin/env python3
"""Dashboard WebUI smoke test.

Exercises the FastAPI dashboard surface that powers the current Sidekick UI.
Uses a temp home directory plus local fakes for session/model/workspace data so
the checks stay deterministic and do not require external providers.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Result:
    passed: int = 0
    failed: int = 0


def _bootstrap_repo() -> None:
    import sys

    sys.path.insert(0, REPO)
    from sidekick_app.__main__ import _bootstrap_aliases, _ensure_self_first

    _ensure_self_first()
    _bootstrap_aliases()


def _mark(result: Result, name: str, ok: bool, detail: str = "") -> None:
    if ok:
        result.passed += 1
        print(f"  [OK] {name}")
    else:
        result.failed += 1
        print(f"  [FAIL] {name}")
        if detail:
            print(f"        {detail}")


def _write_temp_home(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    workspace = home / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (home / "config.yaml").write_text(
        "\n".join(
            [
                "model:",
                "  provider: opencode-zen",
                "  default: deepseek-v4-flash",
                "  base_url: https://opencode.ai/zen/v1",
                "  context_length: 4096",
                "terminal:",
                f"  cwd: \"{workspace.as_posix()}\"",
                "auxiliary:",
                "  vision:",
                "    provider: auto",
                "    model: \"\"",
                "    base_url: \"\"",
                "dashboard:",
                "  theme: default",
            ]
        ),
        encoding="utf-8",
    )
    (home / ".env").write_text("FISHAUDIO_API_KEY=test-key\n", encoding="utf-8")


class _FakeSpace:
    def __init__(self, slug: str, name: str, path: str):
        self.slug = slug
        self.name = name
        self._path = path

    def get_project_dir(self) -> str:
        return self._path

    def to_dict(self) -> dict[str, Any]:
        return {"slug": self.slug, "name": self.name, "path": self._path}


class _FakeSessionDB:
    def __init__(self, *args, **kwargs):
        self._sessions: dict[str, dict[str, Any]] = {
            "sess-root": {
                "session_id": "sess-root",
                "id": "sess-root",
                "title": "Root session",
                "started_at": 1000.0,
                "last_active": 1500.0,
                "ended_at": None,
                "messages": [
                    {"role": "user", "content": "How do we keep Nova steady?"},
                    {"role": "assistant", "content": "By keeping continuity and memory aligned."},
                ],
                "parent_session_id": None,
            },
            "sess-child-a": {
                "session_id": "sess-child-a",
                "id": "sess-child-a",
                "title": "Child A",
                "started_at": 2000.0,
                "last_active": 2050.0,
                "ended_at": None,
                "messages": [{"role": "user", "content": "First child"}],
                "parent_session_id": "sess-root",
            },
            "sess-child-b": {
                "session_id": "sess-child-b",
                "id": "sess-child-b",
                "title": "Child B",
                "started_at": 3000.0,
                "last_active": 3050.0,
                "ended_at": None,
                "messages": [{"role": "user", "content": "Latest child"}],
                "parent_session_id": "sess-root",
            },
        }
        self._conn = self
        self.conn = self

    def list_sessions(self, limit: int = 0, offset: int = 0):
        rows = [self._row(sid, session) for sid, session in self._sessions.items()]
        rows.sort(key=lambda row: row["started_at"], reverse=True)
        return rows[offset : offset + limit if limit else None]

    def list_sessions_rich(self, limit: int = 0, offset: int = 0):
        rows = [self._row(sid, session, rich=True) for sid, session in self._sessions.items()]
        rows.sort(key=lambda row: row["started_at"], reverse=True)
        return rows[offset : offset + limit if limit else None]

    def search_messages(self, query: str, limit: int = 20):
        needle = query.replace("*", "").replace('"', "").lower()
        results = []
        for sid, session in self._sessions.items():
            for msg in session["messages"]:
                content = str(msg.get("content", ""))
                if needle in content.lower() or needle in session["title"].lower():
                    results.append(
                        {
                            "session_id": sid,
                            "snippet": content[:80],
                            "role": msg.get("role"),
                            "source": "chat",
                            "model": "deepseek-v4-flash",
                            "session_started": session["started_at"],
                        }
                    )
                    break
        return results[:limit]

    def resolve_session_id(self, session_id: str):
        return session_id if session_id in self._sessions else None

    def get_session(self, session_id: str):
        session = self._sessions.get(session_id)
        if not session:
            return None
        return dict(session)

    def get_messages(self, session_id: str):
        session = self._sessions.get(session_id)
        return list(session["messages"]) if session else []

    def delete_session(self, session_id: str):
        return self._sessions.pop(session_id, None) is not None

    def close(self):
        return None

    def execute(self, query: str):
        query = str(query).lower()
        if "count(*) from sessions" in query:
            return _FakeCursor([(len(self._sessions),)])
        if "select id, parent_session_id, started_at from sessions" in query:
            rows = [
                {
                    "id": sid,
                    "parent_session_id": session.get("parent_session_id"),
                    "started_at": session["started_at"],
                }
                for sid, session in self._sessions.items()
            ]
            return _FakeCursor(rows)
        return _FakeCursor([])

    def _row(self, session_id: str, session: dict[str, Any], rich: bool = False) -> dict[str, Any]:
        row = {
            "session_id": session_id,
            "id": session_id,
            "title": session["title"],
            "started_at": session["started_at"],
            "last_active": session["last_active"],
            "ended_at": session["ended_at"],
            "message_count": len(session["messages"]),
        }
        if rich:
            row["parent_session_id"] = session.get("parent_session_id")
        return row


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        if not self._rows:
            return (0,)
        first = self._rows[0]
        if isinstance(first, tuple):
            return first
        return tuple(first.values())

    def fetchall(self):
        return list(self._rows)


def _install_fakes(web_server, home: Path):
    import cli.model_switch as model_switch
    import runtime._compat.shim_state as shim_state
    import web.api.config as config_api
    from web.api import space_engine

    web_server.load_workspaces = lambda: [{"path": r"C:\\sidekick\\home\\workspace", "name": "Home"}]
    web_server.get_last_workspace = lambda: r"C:\\sidekick\\home\\workspace"
    web_server._resolve_workspace_path = lambda: home / "workspace"
    space_engine.get_all_workspaces = lambda: [
        _FakeSpace("nova", "Nova", r"C:\\sidekick\\home\\spaces\\nova"),
        _FakeSpace("research", "Research", r"C:\\sidekick\\home\\spaces\\research"),
    ]
    web_server._is_old_frontend = lambda: False

    shim_state.SessionDB = _FakeSessionDB

    web_server._proxy_sync = lambda method, path, headers, body: (
        200,
        b"{\"ok\":true}",
        {
            "Content-Type": "application/json; charset=utf-8",
            "Set-Cookie": "profile=default; Path=/; SameSite=Lax",
            "Content-Disposition": 'attachment; filename="session.json"',
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "close",
        },
        "application/json; charset=utf-8",
    )
    web_server._proxy_stream = lambda method, path, headers, body: iter(
        [b"event: ping\n", b"data: {}\n", b"\n"]
    )

    model_switch.list_authenticated_providers = lambda **kwargs: [
        {
            "provider": "opencode-zen",
            "provider_id": "opencode-zen",
            "models": [
                {"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"},
                {"id": "glm-5", "label": "GLM 5"},
            ],
        }
    ]

    config_api.get_available_models = lambda: {
        "active_provider": "opencode-zen",
        "default_model": "deepseek-v4-flash",
        "configured_model_badges": {},
        "groups": [
            {
                "provider": "OpenCode Zen",
                "provider_id": "opencode-zen",
                "models": [{"id": "deepseek-v4-flash", "label": "DeepSeek V4 Flash"}],
                "extra_models": [{"id": "glm-5", "label": "GLM 5"}],
            }
        ],
    }


def run_smoke() -> Result:
    _bootstrap_repo()
    result = Result()

    with tempfile.TemporaryDirectory(prefix="sidekick-webui-smoke-") as tmp:
        home = Path(tmp) / "home"
        _write_temp_home(home)
        os.environ["SIDEKICK_HOME"] = str(home)
        os.environ["SIDEKICK_WEBUI_TEST_NETWORK_BLOCK"] = "1"

        from cli import web_server

        _install_fakes(web_server, home)
        client = TestClient(web_server.app)
        headers = {"X-Hermes-Session-Token": web_server._SESSION_TOKEN}

        def get(path: str):
            return client.get(path, headers=headers)

        def post(path: str, body: dict[str, Any] | None = None):
            return client.post(path, headers=headers, json=body or {})

        def put(path: str, body: dict[str, Any] | None = None):
            return client.put(path, headers=headers, json=body or {})

        def delete(path: str, body: dict[str, Any] | None = None):
            return client.request("DELETE", path, headers=headers, json=body or {})

        resp = get("/health")
        payload = resp.json()
        _mark(result, "/health", resp.status_code == 200 and payload.get("service") == "sidekick-dashboard", f"status={resp.status_code}")

        resp = get("/api/status")
        payload = resp.json()
        _mark(
            result,
            "/api/status",
            resp.status_code == 200 and payload.get("version") and "active_sessions" in payload,
            f"status={resp.status_code}",
        )

        resp = get("/api/workspaces")
        payload = resp.json()
        workspaces = payload.get("workspaces", [])
        _mark(result, "/api/workspaces", resp.status_code == 200 and len(workspaces) >= 2 and payload.get("last"), f"status={resp.status_code}")

        resp = get("/api/spaces")
        payload = resp.json()
        spaces = payload.get("spaces", [])
        _mark(result, "/api/spaces", resp.status_code == 200 and any(space.get("slug") == "nova" for space in spaces), f"status={resp.status_code}")

        resp = get("/api/workspace/git-status")
        payload = resp.json()
        _mark(result, "/api/workspace/git-status", resp.status_code == 200 and payload.get("is_git_repo") is False, f"status={resp.status_code}")

        resp = get("/api/sessions")
        payload = resp.json()
        sessions = payload.get("sessions", [])
        _mark(result, "/api/sessions", resp.status_code == 200 and len(sessions) >= 3, f"status={resp.status_code}")

        resp = get("/api/sessions/search?q=Nova")
        payload = resp.json()
        _mark(result, "/api/sessions/search", resp.status_code == 200 and payload.get("results"), f"status={resp.status_code}")

        resp = get("/api/sessions/sess-root")
        payload = resp.json()
        _mark(result, "/api/sessions/{id}", resp.status_code == 200 and payload.get("session_id") == "sess-root", f"status={resp.status_code}")

        resp = get("/api/sessions/sess-root/latest-descendant")
        payload = resp.json()
        _mark(result, "/api/sessions/{id}/latest-descendant", resp.status_code == 200 and payload.get("session_id") == "sess-child-b" and payload.get("changed") is True, f"status={resp.status_code}")

        resp = get("/api/sessions/sess-root/messages")
        payload = resp.json()
        _mark(result, "/api/sessions/{id}/messages", resp.status_code == 200 and len(payload.get("messages", [])) == 2, f"status={resp.status_code}")

        resp = delete("/api/sessions/sess-child-a")
        _mark(result, "/api/sessions/{id} DELETE", resp.status_code == 200 and resp.json().get("ok") is True, f"status={resp.status_code}")

        resp = get("/api/config")
        _mark(result, "/api/config", resp.status_code == 200 and resp.json().get("model"), f"status={resp.status_code}")

        resp = get("/api/config/defaults")
        _mark(result, "/api/config/defaults", resp.status_code == 200 and "model" in resp.json(), f"status={resp.status_code}")

        resp = get("/api/config/schema")
        _mark(result, "/api/config/schema", resp.status_code == 200 and "fields" in resp.json(), f"status={resp.status_code}")

        resp = get("/api/config/raw")
        _mark(result, "/api/config/raw GET", resp.status_code == 200 and "yaml" in resp.json(), f"status={resp.status_code}")

        resp = put("/api/config/raw", {"yaml_text": "model:\n  default: deepseek-v4-flash\n"})
        _mark(result, "/api/config/raw PUT", resp.status_code == 200 and resp.json().get("ok") is True, f"status={resp.status_code}")

        resp = get("/api/models")
        payload = resp.json()
        _mark(result, "/api/models", resp.status_code == 200 and payload.get("active_provider") == "opencode-zen", f"status={resp.status_code}")

        resp = get("/api/models/live?provider=opencode-zen")
        payload = resp.json()
        _mark(result, "/api/models/live", resp.status_code == 200 and payload.get("count") == 2, f"status={resp.status_code}")

        resp = post("/api/models/refresh")
        _mark(result, "/api/models/refresh", resp.status_code == 200 and resp.json().get("ok") is True, f"status={resp.status_code}")

        resp = get("/api/model/info")
        payload = resp.json()
        _mark(result, "/api/model/info", resp.status_code == 200 and payload.get("model") == "deepseek-v4-flash", f"status={resp.status_code}")

        resp = get("/api/model/options")
        payload = resp.json()
        _mark(result, "/api/model/options", resp.status_code == 200 and payload.get("providers"), f"status={resp.status_code}")

        resp = get("/api/model/auxiliary")
        payload = resp.json()
        _mark(result, "/api/model/auxiliary", resp.status_code == 200 and payload.get("main", {}).get("model") == "deepseek-v4-flash", f"status={resp.status_code}")

        resp = post("/api/model/set", {"scope": "main", "provider": "opencode-zen", "model": "glm-5"})
        _mark(result, "/api/model/set main", resp.status_code == 200 and resp.json().get("ok") is True, f"status={resp.status_code}")

        resp = post("/api/model/set", {"scope": "auxiliary", "provider": "opencode-zen", "model": "glm-5", "task": "vision"})
        _mark(result, "/api/model/set auxiliary", resp.status_code == 200 and resp.json().get("ok") is True, f"status={resp.status_code}")

        resp = get("/api/env")
        _mark(result, "/api/env", resp.status_code == 200 and isinstance(resp.json(), dict), f"status={resp.status_code}")

        resp = get("/api/dashboard/themes")
        payload = resp.json()
        _mark(result, "/api/dashboard/themes", resp.status_code == 200 and isinstance(payload.get("themes"), list), f"status={resp.status_code}")

        resp = get("/api/dashboard/plugins")
        payload = resp.json()
        _mark(result, "/api/dashboard/plugins", resp.status_code == 200 and isinstance(payload, list), f"status={resp.status_code}")

        resp = get("/api/dashboard/plugins/rescan")
        payload = resp.json()
        _mark(result, "/api/dashboard/plugins/rescan", resp.status_code == 200 and payload.get("ok") is True and isinstance(payload.get("count"), int), f"status={resp.status_code}")

        resp = get("/api/dashboard/plugins/hub")
        _mark(result, "/api/dashboard/plugins/hub", resp.status_code == 200 and isinstance(resp.json(), dict), f"status={resp.status_code}")

        resp = get("/api/nova/status")
        payload = resp.json()
        _mark(result, "/api/nova/status", resp.status_code == 200 and payload.get("autonomy_level") == 2, f"status={resp.status_code}")

        resp = get("/api/nova/personality")
        payload = resp.json()
        _mark(result, "/api/nova/personality", resp.status_code == 200 and "traits" in payload, f"status={resp.status_code}")

        resp = get("/api/nova/events")
        payload = resp.json()
        _mark(result, "/api/nova/events", resp.status_code == 200 and "events" in payload, f"status={resp.status_code}")

        resp = client.get("/api/chat/stream?stream_id=s1", headers=headers)
        _mark(result, "/api/chat/stream", resp.status_code == 200 and "event: ping" in resp.text, f"status={resp.status_code}")

        resp = client.get("/api/not-native-route", headers=headers)
        payload = resp.json()
        _mark(result, "proxy header passthrough", resp.status_code == 200 and payload.get("ok") is True and resp.headers.get("set-cookie"), f"status={resp.status_code}")

    return result


def main() -> int:
    print("=== Dashboard WebUI smoke ===\n")
    result = run_smoke()
    print(f"\n=== Ergebnis: {result.passed} passed, {result.failed} failed ===")
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
