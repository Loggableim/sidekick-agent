"""Webhook platform adapter."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Optional

from runtime.gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType
from shared.constants import get_sidekick_home

logger = logging.getLogger(__name__)


def check_webhook_requirements() -> bool:
    try:
        from aiohttp import web  # noqa: F401
        return True
    except ImportError:
        return False


class WebhookAdapter(BasePlatformAdapter):
    """HTTP webhook adapter for dynamic and configured routes."""

    def __init__(self, config: Any = None):
        super().__init__(config)
        extra = getattr(config, "extra", {}) or {}
        self._host = str(extra.get("host") or "0.0.0.0")
        self._port = int(extra.get("port") or 8644)
        self._global_secret = str(extra.get("secret") or "")
        self._config_routes = extra.get("routes") if isinstance(extra.get("routes"), dict) else {}
        self._msg_handler: Optional[Callable] = None
        self._fatal_handler: Optional[Callable] = None
        self._session_store: Any = None
        self._busy_handler: Optional[Callable] = None
        self._runner: Any = None
        self._site: Any = None

    def set_message_handler(self, h):
        self._msg_handler = h

    def set_fatal_error_handler(self, h):
        self._fatal_handler = h

    def set_session_store(self, s):
        self._session_store = s

    def set_busy_session_handler(self, h):
        self._busy_handler = h

    async def connect(self) -> bool:
        return await self.start()

    async def disconnect(self):
        await self.stop()

    async def start(self) -> bool:
        try:
            from aiohttp import web

            app = web.Application()
            app.router.add_get("/health", self._handle_health)
            app.router.add_post("/webhooks/{name}", self._handle_webhook)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, self._host, self._port)
            await site.start()
            self._runner = runner
            self._site = site
            logger.info("Webhook: listening on %s:%s", self._host, self._port)
            return True
        except Exception as exc:
            self._fatal_error_code = type(exc).__name__
            self._fatal_error_message = str(exc)
            logger.error("Webhook start failed: %s", exc, exc_info=True)
            await self.stop()
            return False

    async def stop(self):
        runner = self._runner
        self._site = None
        self._runner = None
        if runner is not None:
            await runner.cleanup()

    async def send(self, chat_id: str, message: str, **kwargs) -> dict:
        return {"success": False, "error": "Webhook platform is inbound-only"}

    async def _handle_health(self, _request):
        from aiohttp import web

        return web.json_response({"ok": True, "platform": "webhook"})

    async def _handle_webhook(self, request):
        from aiohttp import web

        name = str(request.match_info.get("name", "")).strip().lower()
        route = self._load_routes().get(name)
        if not route:
            return web.json_response({"error": "unknown webhook route"}, status=404)

        body = await request.read()
        secret = str(route.get("secret") or self._global_secret or "")
        if secret and not _valid_signature(body, secret, request.headers):
            return web.json_response({"error": "invalid signature"}, status=401)

        payload = _decode_payload(body)
        event_type = _event_type(request.headers, payload)
        allowed_events = route.get("events") or []
        if allowed_events and event_type not in {str(e) for e in allowed_events}:
            return web.json_response({"ok": True, "ignored": True, "event": event_type})

        if self._msg_handler is None:
            return web.json_response({"error": "webhook handler not registered"}, status=503)

        event = MessageEvent(
            platform="webhook",
            chat_id=name,
            user_id=str(getattr(request, "remote", "") or "webhook"),
            message_id=f"{name}-{int(time.time() * 1000)}",
            text=_event_text(route, payload, event_type),
            chat_name=name,
            user_name=event_type or "webhook",
            chat_type="webhook",
            message_type=MessageType.TEXT,
            raw={
                "route": name,
                "event_type": event_type,
                "payload": payload,
                "deliver": route.get("deliver", "log"),
                "deliver_only": bool(route.get("deliver_only")),
            },
        )
        await self._msg_handler(event)
        return web.json_response({"ok": True, "route": name, "event": event_type})

    def _load_routes(self) -> dict[str, dict]:
        routes: dict[str, dict] = {
            str(name).strip().lower(): value
            for name, value in self._config_routes.items()
            if isinstance(value, dict)
        }
        sub_path = Path(get_sidekick_home()) / "webhook_subscriptions.json"
        try:
            data = json.loads(sub_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for name, value in data.items():
                    if isinstance(value, dict):
                        routes[str(name).strip().lower()] = value
        except FileNotFoundError:
            pass
        except Exception:
            logger.debug("Failed to load webhook subscriptions", exc_info=True)
        return routes


def _valid_signature(body: bytes, secret: str, headers: Any) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    supplied = (
        headers.get("X-Hub-Signature-256")
        or headers.get("X-Sidekick-Signature-256")
        or headers.get("X-Signature-256")
        or ""
    )
    return hmac.compare_digest(str(supplied), expected)


def _decode_payload(body: bytes) -> Any:
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except Exception:
        return {"raw": body.decode("utf-8", errors="replace")}


def _event_type(headers: Any, payload: Any) -> str:
    if headers.get("X-GitHub-Event"):
        return str(headers.get("X-GitHub-Event"))
    if isinstance(payload, dict):
        return str(payload.get("event_type") or payload.get("action") or "webhook")
    return "webhook"


def _event_text(route: dict, payload: Any, event_type: str) -> str:
    prompt = str(route.get("prompt") or "").strip()
    payload_text = json.dumps(payload, ensure_ascii=False, indent=2)
    if prompt:
        return f"{prompt}\n\nEvent: {event_type}\nPayload:\n{payload_text}"
    return f"Webhook event: {event_type}\nPayload:\n{payload_text}"
