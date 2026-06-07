from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from shared.config import runtime_summary
from shared.agent_bridge import run_assistant_once
from shared.logging_setup import setup_logging
from shared.runtime import build_runtime_report, build_web_runtime
from shared.sessions import (
    append_message,
    delete_session,
    list_sessions,
    load_session,
    new_session,
    update_session,
)

logger = logging.getLogger(__name__)

_INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Sidekick</title>
  <style>
    :root { color-scheme: light; --bg:#f5f1e8; --panel:#fffdf8; --ink:#1d1a16; --line:#d7cbb9; --accent:#b4492f; }
    body { margin:0; font-family: Georgia, 'Times New Roman', serif; background:linear-gradient(180deg,#efe3cd 0%,#f8f4ec 100%); color:var(--ink); }
    main { max-width:960px; margin:0 auto; padding:32px 20px 48px; }
    .shell { display:grid; gap:18px; grid-template-columns:320px 1fr; }
    .card { background:var(--panel); border:1px solid var(--line); border-radius:18px; padding:18px; box-shadow:0 12px 40px rgba(48,29,10,.08); }
    h1,h2 { margin:0 0 12px; }
    button,input,textarea { font:inherit; }
    button { background:var(--accent); color:white; border:none; border-radius:999px; padding:10px 14px; cursor:pointer; }
    input,textarea { width:100%; box-sizing:border-box; padding:10px 12px; border-radius:12px; border:1px solid var(--line); background:#fff; }
    ul { list-style:none; margin:0; padding:0; display:grid; gap:10px; }
    li { border:1px solid var(--line); border-radius:14px; padding:12px; cursor:pointer; background:#fff; }
    li.active { outline:2px solid var(--accent); }
    .muted { color:#6f6558; font-size:14px; }
    .row { display:flex; gap:10px; align-items:center; }
    .stack { display:grid; gap:10px; }
    pre { white-space:pre-wrap; }
    @media (max-width: 820px) { .shell { grid-template-columns:1fr; } }
  </style>
</head>
<body>
<main>
  <h1>Sidekick</h1>
  <p class="muted">Minimal monorepo web surface backed by the new unified API.</p>
  <div class="shell">
    <section class="card stack">
      <div class="row">
        <input id="title" placeholder="New session title">
        <button id="create">New</button>
      </div>
      <ul id="sessions"></ul>
    </section>
    <section class="card stack">
      <h2 id="sessionTitle">No session selected</h2>
      <div class="muted" id="sessionMeta">Create or select a session.</div>
      <div class="stack">
        <textarea id="message" rows="5" placeholder="Write a message to store in the session log"></textarea>
        <div class="row">
          <button id="send">Add message</button>
          <button id="rename">Rename</button>
          <button id="remove">Delete</button>
        </div>
      </div>
      <pre id="detail" class="muted"></pre>
    </section>
  </div>
</main>
<script>
  const state = { selectedId: null, sessions: [] };
  const $ = (id) => document.getElementById(id);

  async function api(path, options = {}) {
    const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  }

  function renderSessions() {
    const list = $("sessions");
    list.innerHTML = "";
    for (const session of state.sessions) {
      const item = document.createElement("li");
      item.className = session.session_id === state.selectedId ? "active" : "";
      item.innerHTML = `<strong>${session.title}</strong><div class="muted">${session.message_count} messages</div>`;
      item.onclick = () => loadSession(session.session_id);
      list.appendChild(item);
    }
  }

  async function refreshSessions() {
    const data = await api("/api/sessions");
    state.sessions = data.sessions;
    renderSessions();
  }

  async function loadSession(sessionId) {
    const data = await api(`/api/session?id=${encodeURIComponent(sessionId)}`);
    state.selectedId = sessionId;
    $("sessionTitle").textContent = data.session.title;
    $("sessionMeta").textContent = `${data.session.workspace} · ${data.session.model}`;
    $("detail").textContent = JSON.stringify(data.session.messages, null, 2);
    renderSessions();
  }

  $("create").onclick = async () => {
    const title = $("title").value.trim();
    const created = await api("/api/sessions", { method: "POST", body: JSON.stringify({ title }) });
    $("title").value = "";
    await refreshSessions();
    await loadSession(created.session.session_id);
  };

  $("send").onclick = async () => {
    if (!state.selectedId) return;
    const content = $("message").value.trim();
    if (!content) return;
    await api(`/api/session/chat?id=${encodeURIComponent(state.selectedId)}`, {
      method: "POST",
      body: JSON.stringify({ content })
    });
    $("message").value = "";
    await refreshSessions();
    await loadSession(state.selectedId);
  };

  $("rename").onclick = async () => {
    if (!state.selectedId) return;
    const title = prompt("New session title:");
    if (!title) return;
    await api(`/api/session?id=${encodeURIComponent(state.selectedId)}`, {
      method: "PATCH",
      body: JSON.stringify({ title })
    });
    await refreshSessions();
    await loadSession(state.selectedId);
  };

  $("remove").onclick = async () => {
    if (!state.selectedId) return;
    await api(`/api/session?id=${encodeURIComponent(state.selectedId)}`, { method: "DELETE" });
    state.selectedId = null;
    $("sessionTitle").textContent = "No session selected";
    $("sessionMeta").textContent = "Create or select a session.";
    $("detail").textContent = "";
    await refreshSessions();
  };

  refreshSessions();
</script>
</body>
</html>
"""


def _json(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class SidekickWebHandler(BaseHTTPRequestHandler):
    server_version = "SidekickWeb/0.1"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            _json(
                self,
                {
                    "ok": True,
                    "service": "sidekick-web",
                    "path": parsed.path,
                },
            )
            return

        if parsed.path == "/api/runtime-summary":
            repo_root = Path(__file__).resolve().parents[1]
            _json(
                self,
                {
                    "ok": True,
                    "runtime": build_runtime_report(repo_root),
                    "config": runtime_summary(),
                },
            )
            return

        if parsed.path == "/api/sessions":
            _json(self, {"ok": True, "sessions": list_sessions()})
            return

        if parsed.path == "/api/session":
            query = parse_qs(parsed.query)
            session_id = (query.get("id") or [""])[0].strip()
            if not session_id:
                _json(self, {"ok": False, "error": "missing session id"}, status=400)
                return
            session = load_session(session_id)
            if session is None:
                _json(self, {"ok": False, "error": "session not found"}, status=404)
                return
            _json(self, {"ok": True, "session": session.__dict__})
            return

        if parsed.path == "/":
            body = _INDEX_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        _json(self, {"ok": False, "error": "not found", "path": parsed.path}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            _json(self, {"ok": False, "error": "invalid json body"}, status=400)
            return

        if parsed.path == "/api/sessions":
            session = new_session(
                title=payload.get("title"),
                workspace=payload.get("workspace"),
                model=payload.get("model"),
            )
            _json(self, {"ok": True, "session": session.__dict__}, status=201)
            return

        if parsed.path == "/api/session/messages":
            query = parse_qs(parsed.query)
            session_id = (query.get("id") or [""])[0].strip()
            if not session_id:
                _json(self, {"ok": False, "error": "missing session id"}, status=400)
                return
            role = str(payload.get("role") or "user")
            content = str(payload.get("content") or "")
            session = append_message(session_id, role=role, content=content)
            if session is None:
                _json(self, {"ok": False, "error": "session not found"}, status=404)
                return
            _json(self, {"ok": True, "session": session.__dict__}, status=201)
            return

        if parsed.path == "/api/session/chat":
            query = parse_qs(parsed.query)
            session_id = (query.get("id") or [""])[0].strip()
            if not session_id:
                _json(self, {"ok": False, "error": "missing session id"}, status=400)
                return
            content = str(payload.get("content") or "")
            if not content.strip():
                _json(self, {"ok": False, "error": "missing message content"}, status=400)
                return
            session = append_message(session_id, role="user", content=content)
            if session is None:
                _json(self, {"ok": False, "error": "session not found"}, status=404)
                return
            bridge = run_assistant_once(content)
            session = append_message(
                session_id,
                role="assistant",
                content=bridge.reply,
            )
            _json(
                self,
                {
                    "ok": bridge.ok,
                    "bridge_backend": bridge.backend,
                    "bridge_error": bridge.error,
                    "session": session.__dict__ if session else None,
                },
                status=201 if bridge.ok else 202,
            )
            return

        _json(self, {"ok": False, "error": "not found", "path": parsed.path}, status=404)

    def do_PATCH(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/session":
            _json(self, {"ok": False, "error": "not found", "path": parsed.path}, status=404)
            return
        query = parse_qs(parsed.query)
        session_id = (query.get("id") or [""])[0].strip()
        if not session_id:
            _json(self, {"ok": False, "error": "missing session id"}, status=400)
            return
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(content_length) if content_length else b"{}"
        try:
            payload = json.loads(body.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            _json(self, {"ok": False, "error": "invalid json body"}, status=400)
            return
        session = update_session(
            session_id,
            title=payload.get("title"),
            workspace=payload.get("workspace"),
            model=payload.get("model"),
        )
        if session is None:
            _json(self, {"ok": False, "error": "session not found"}, status=404)
            return
        _json(self, {"ok": True, "session": session.__dict__})

    def do_DELETE(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path != "/api/session":
            _json(self, {"ok": False, "error": "not found", "path": parsed.path}, status=404)
            return
        query = parse_qs(parsed.query)
        session_id = (query.get("id") or [""])[0].strip()
        if not session_id:
            _json(self, {"ok": False, "error": "missing session id"}, status=400)
            return
        if not delete_session(session_id):
            _json(self, {"ok": False, "error": "session not found"}, status=404)
            return
        _json(self, {"ok": True, "deleted": session_id})

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        logger.info("%s - %s", self.address_string(), format % args)


def create_server() -> ThreadingHTTPServer:
    repo_root = Path(__file__).resolve().parents[1]
    runtime = build_web_runtime(repo_root)
    return ThreadingHTTPServer((runtime.host, runtime.port), SidekickWebHandler)


def serve_forever() -> None:
    setup_logging()
    server = create_server()
    host, port = server.server_address[:2]
    logger.info("sidekick web server listening on %s:%s", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
