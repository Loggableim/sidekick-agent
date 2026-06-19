#!/usr/bin/env python3
"""Legacy WebUI HTTP smoke test for Sidekick.

This script exercises the old stdlib web surface that still backs the
compatibility proxy. It is intentionally importable so ``tests/smoke_all.py``
can call ``main()`` without executing on import.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@dataclass
class Result:
    passed: int = 0
    failed: int = 0


def _bootstrap_repo() -> None:
    sys.path.insert(0, REPO)
    from sidekick_app.__main__ import _bootstrap_aliases, _ensure_self_first

    _ensure_self_first()
    _bootstrap_aliases()


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _request_json(port: int, method: str, path: str, body: Any | None = None) -> tuple[int, Any]:
    url = f"http://127.0.0.1:{port}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            if "application/json" in ctype:
                return resp.status, json.loads(raw.decode("utf-8"))
            return resp.status, raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw.decode("utf-8"))
        except Exception:
            return exc.code, raw.decode("utf-8", errors="replace")
    except Exception as exc:
        return 0, {"error": str(exc)}


def _mark(result: Result, name: str, ok: bool, detail: str = "") -> None:
    if ok:
        result.passed += 1
        print(f"  [OK] {name}")
    else:
        result.failed += 1
        print(f"  [FAIL] {name}")
        if detail:
            print(f"        {detail}")


def run_smoke() -> Result:
    _bootstrap_repo()
    from web.server import create_server

    result = Result()
    port = _find_free_port()
    os.environ["SIDEKICK_WEBUI_HOST"] = "127.0.0.1"
    os.environ["SIDEKICK_WEBUI_PORT"] = str(port)
    os.environ.setdefault("HERMES_WEBUI_LOG_FILE", os.devnull)

    server = create_server("127.0.0.1", port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        code, data = _request_json(port, "GET", "/health")
        _mark(result, "/health", code == 200 and isinstance(data, dict), f"status={code} data={str(data)[:120]}")

        code, _ = _request_json(port, "GET", "/favicon.ico")
        _mark(result, "favicon", code == 200, f"status={code}")

        code, created = _request_json(port, "POST", "/api/session/new", {"title": "smoke-legacy"})
        session = created.get("session", created) if isinstance(created, dict) else {}
        session_id = session.get("session_id") or session.get("id") or ""
        _mark(result, "session create", code in {200, 201} and bool(session_id), f"status={code} id={session_id}")

        if session_id:
            # Match the production session-switch fast path. Full message/model
            # hydration is covered by targeted tests and can legitimately touch
            # slow external model metadata caches on a developer machine.
            code, loaded = _request_json(
                port,
                "GET",
                f"/api/session?session_id={session_id}&messages=0&resolve_model=0",
            )
            session_data = loaded.get("session", {}) if isinstance(loaded, dict) else {}
            _mark(result, "session load", code == 200 and session_data.get("session_id") == session_id, f"status={code}")

            code, listed = _request_json(port, "GET", "/api/sessions")
            _mark(result, "session list", code == 200, f"status={code}")

        code, _ = _request_json(port, "GET", "/api/sessions")
        _mark(result, "sessions endpoint", code == 200, f"status={code}")

        code, _ = _request_json(port, "GET", "/api/sessions/search?q=smoke")
        _mark(result, "sessions search", code == 200, f"status={code}")

        code, _ = _request_json(port, "GET", "/")
        _mark(result, "root html", code == 200, f"status={code}")

        code, _ = _request_json(port, "GET", "/api/logs?file=agent&lines=5")
        _mark(result, "logs endpoint", code == 200, f"status={code}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    return result


def main() -> int:
    print("=== Legacy WebUI smoke ===\n")
    result = run_smoke()
    print(f"\n=== Ergebnis: {result.passed} passed, {result.failed} failed ===")
    return 0 if result.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
