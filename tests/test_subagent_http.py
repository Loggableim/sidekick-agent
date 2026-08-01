"""Read-only contracts for the session-scoped subagent journal API."""

from __future__ import annotations

import io
import json
from pathlib import Path
from urllib.parse import urlparse

from tools.subagent_store import SubagentStore


class _Handler:
    client_address = ("127.0.0.1", 12345)

    def __init__(self, *, headers=None, writer=None) -> None:
        self.headers = {"Host": "127.0.0.1", "X-Sidekick-Workspace": "default", **(headers or {})}
        self.status_code = None
        self.response_headers = {}
        self.rfile = io.BytesIO()
        self.wfile = writer or io.BytesIO()

    def send_response(self, status, *_args):
        self.status_code = status

    def send_header(self, name, value):
        self.response_headers[name.lower()] = value

    def end_headers(self):
        return None


class _CloseAfterWrite(io.BytesIO):
    def __init__(self, close_after: int = 2) -> None:
        super().__init__()
        self._writes = 0
        self._close_after = close_after

    def write(self, data):
        self._writes += 1
        return super().write(data)

    def is_closed(self):
        return self._writes >= self._close_after


def _payload(handler: _Handler) -> dict:
    return json.loads(handler.wfile.getvalue().decode("utf-8"))


def _activate_session(home: Path, session_id: str, slug: str = "default") -> None:
    sessions = home / "spaces" / slug / "sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text("{}", encoding="utf-8")

def _seed(store: SubagentStore) -> None:
    store.record_run(
        subagent_id="sa-one", session_id="chat-one", space_slug="default", parent_id="parent",
        goal="secret=should-not-leak", role="builder", model="model-a", status="running",
    )
    store.append_event("sa-one", 1, "started", "C:\\private\\token.txt")
    store.record_run(
        subagent_id="sa-two", session_id="chat-two", space_slug="other", parent_id="parent",
        goal="other", role="reviewer", model="model-b", status="completed",
    )
    store.append_event("sa-two", 1, "completed", "other")


def test_subagent_list_and_detail_are_session_scoped_and_read_only(monkeypatch, tmp_path: Path):
    """A current-chat query cannot see another chat and never opens writable SQLite."""
    home = tmp_path / "home"
    _seed(SubagentStore(home))
    _activate_session(home, "chat-one")
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    from web.api import routes

    before = sorted(path.name for path in home.iterdir())
    handler = _Handler()
    assert routes.handle_get(handler, urlparse("/api/subagents?session_id=chat-one&status=all&limit=50")) is None
    body = _payload(handler)
    assert handler.status_code == 200
    assert [item["subagent_id"] for item in body["runs"]] == ["sa-one"]
    assert "should-not-leak" not in body["runs"][0]["goal_summary"]
    assert sorted(path.name for path in home.iterdir()) == before

    handler = _Handler()
    assert routes.handle_get(handler, urlparse("/api/subagents/sa-two?session_id=chat-one")) is None
    assert handler.status_code == 404


def test_subagent_list_rejects_bad_session_status_and_cursor(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    _seed(SubagentStore(home))
    _activate_session(home, "chat-one")
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    from web.api import routes

    for path in (
        "/api/subagents?status=all",
        "/api/subagents?session_id=chat-one&status=surprise",
        "/api/subagents?session_id=chat-one&cursor=" + ("x" * 129),
        "/api/subagents?session_id=chat-one&limit=51",
    ):
        handler = _Handler()
        assert routes.handle_get(handler, urlparse(path)) is None
        assert handler.status_code == 400


def test_subagent_sse_sends_only_session_events_and_snapshot_on_gap(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    store = SubagentStore(home)
    _seed(store)
    _activate_session(home, "chat-one")
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    from web.api import routes
    monkeypatch.setattr(routes, "_SUBAGENT_SSE_POLL_SECONDS", 0)

    writer = _CloseAfterWrite(close_after=2)
    handler = _Handler(headers={"Last-Event-ID": "0"}, writer=writer)
    assert routes.handle_get(handler, urlparse("/api/subagents/events/stream?session_id=chat-one")) is True
    frame = writer.getvalue().decode("utf-8")
    assert "event: hello" in frame
    assert '"subagent_id": "sa-one"' in frame
    assert "sa-two" not in frame

    writer = _CloseAfterWrite(close_after=2)
    handler = _Handler(headers={"Last-Event-ID": "999999"}, writer=writer)
    assert routes.handle_get(handler, urlparse("/api/subagents/events/stream?session_id=chat-one")) is True
    assert "event: snapshot" in writer.getvalue().decode("utf-8")


def test_subagent_sse_uses_snapshot_for_a_per_run_sequence_gap(monkeypatch, tmp_path: Path):
    home = tmp_path / "home"
    store = SubagentStore(home)
    _seed(store)
    _activate_session(home, "chat-one")
    store.append_event("sa-one", 3, "progress", "gap")
    _activate_session(home, "chat-one")
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    from web.api import routes
    monkeypatch.setattr(routes, "_SUBAGENT_SSE_POLL_SECONDS", 0)
    writer = _CloseAfterWrite(close_after=2)
    handler = _Handler(writer=writer)
    assert routes.handle_get(handler, urlparse("/api/subagents/events/stream?session_id=chat-one")) is True
    assert "event: snapshot" in writer.getvalue().decode("utf-8")