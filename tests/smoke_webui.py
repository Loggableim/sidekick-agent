#!/usr/bin/env python3
"""WebUI HTTP Smoke Test for Sidekick v0.4.0.

Creates a minimal web server instance programmatically and runs HTTP checks.
All tests work without any running Sidekick instance.

Usage:
    python tests/smoke_webui.py
"""
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASS = 0
FAIL = 0


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]

def http_get(port: int, path: str) -> tuple[int, dict | str]:
    """GET an endpoint, return (status_code, parsed_data_or_raw)."""
    url = f"http://127.0.0.1:{port}{path}"
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        raw = resp.read()
        ct = resp.headers.get("Content-Type", "")
        if "application/json" in ct:
            return resp.status, json.loads(raw.decode("utf-8"))
        return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return e.code, raw
    except (urllib.error.URLError, ConnectionRefusedError, OSError) as e:
        return 0, {"error": str(e)}


def test(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name}")
        if detail:
            print(f"    → {detail}")


print("=== WebUI HTTP Smoke Test ===\n")

# Set TESTING env so server uses minimal startup
os.environ.setdefault("HERMES_WEBUI_LOG_FILE", os.devnull)

port = find_free_port()
os.environ["SIDEKICK_WEBUI_PORT"] = str(port)
os.environ["SIDEKICK_WEBUI_HOST"] = "127.0.0.1"

print(f"Starting server on http://127.0.0.1:{port} ...")

# Bootstrap + create server programmatically (avoids main() ceremony)
sys.path.insert(0, REPO)

# Pop old sys.path entries to ensure correct module resolution
from sidekick_app.__main__ import _ensure_self_first, _bootstrap_aliases
_ensure_self_first()
_bootstrap_aliases()

from http.server import ThreadingHTTPServer
from web.server import QuietHTTPServer, Handler
server = QuietHTTPServer(("127.0.0.1", port), Handler)
server_thread = threading.Thread(target=server.serve_forever, daemon=True)
server_thread.start()
time.sleep(0.5)  # Brief wait for server to be ready

# ── /health endpoint ──
code, data = http_get(port, "/health")
test("/health returns 200", code == 200 and isinstance(data, dict),
     f"status={code} data={str(data)[:100]}")

# ── Static asset ──
code, data = http_get(port, "/favicon.ico")
test("Static asset (favicon)", code == 200, f"status={code}")

# ── Session create via POST ──
import urllib.request as _ur
url = f"http://127.0.0.1:{port}/api/session/new"
body = json.dumps({"title": "smoke-test-v040"}).encode()
req = _ur.Request(url, data=body,
    headers={"Content-Type": "application/json"})
try:
    resp = _ur.urlopen(req, timeout=10)
    created = json.loads(resp.read().decode())
    s = created.get("session", created)
    session_id = s.get("session_id", s.get("id", ""))
    test("Session created", bool(session_id) and resp.status in (200, 201),
         f"status={resp.status} id={session_id[:20] if session_id else 'NONE'}")
except Exception as e:
    test("Session created", False, str(e))

# ── Sessions list ──
code, data = http_get(port, "/api/sessions")
sessions_list = data if isinstance(data, list) else data.get("sessions", []) if isinstance(data, dict) else []
test("Sessions list", code == 200, f"status={code} count={len(sessions_list)}")

# ── Server stop ──
server.shutdown()
server.server_close()
test("Server stops cleanly", True)

print(f"\n─── Ergebnis: {PASS} passed, {FAIL} failed ───")
raise SystemExit(0 if FAIL == 0 else 1)
