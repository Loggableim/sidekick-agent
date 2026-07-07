from __future__ import annotations

import atexit
import asyncio
import base64
import dataclasses
import ipaddress
import logging
import os
import queue
import re
import secrets
import struct
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_LOOP_THREAD: threading.Thread | None = None
_LOOP: asyncio.AbstractEventLoop | None = None
_READY = threading.Event()
_MANAGER: "BrowserManager | None" = None
_PERMISSIONS: dict[str, dict[str, Any]] = {}
_GLOBAL_PERMISSION: dict[str, Any] = {}
_PERMISSIONS_LOCK = threading.Lock()

_DEFAULT_VIEWPORT = {"width": 1440, "height": 900}
_DEFAULT_ALLOWED_HOST_PATTERNS = (
    "localhost",
    "127.0.0.1",
    "::1",
    "*.localhost",
    "*.local",
    "*.lan",
    "*.internal",
)
_HEARTBEAT_INTERVAL_SECONDS = 30.0
_MAX_SUBSCRIBERS_PER_SESSION = 16
_BROWSER_PERMISSION_MODES = {"none", "read", "control"}
_QA_ACTIONS = {"test_current_page", "test-page", "page_test", "qa", "test"}
_READ_ACTIONS = {"snapshot", "state"} | _QA_ACTIONS
_CONTROL_ACTIONS = {
    "navigate",
    "open",
    "click",
    "type",
    "scroll",
    "back",
    "forward",
    "reload",
    "stop",
    "press",
    "move",
    "wait",
    "sequence",
    "action_v1",
    "browser_action_v1",
}
_PERMISSION_TTL_SECONDS = 15 * 60
_SECRET_PATTERN = re.compile(r"(sk-[A-Za-z0-9_-]{16,}|[A-Za-z0-9_]*api[_-]?key[A-Za-z0-9_]*=)", re.IGNORECASE)


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _analyze_png_frame(frame: bytes | None) -> dict[str, Any]:
    if not frame:
        return {"available": False, "findings": ["No screenshot bytes available for visual analysis."]}
    try:
        if not frame.startswith(b"\x89PNG\r\n\x1a\n"):
            return {"available": True, "error": "unsupported image format", "findings": ["Screenshot frame is not a PNG image."]}
        offset = 8
        width = height = bit_depth = color_type = None
        idat = bytearray()
        while offset + 8 <= len(frame):
            length = struct.unpack(">I", frame[offset:offset + 4])[0]
            chunk_type = frame[offset + 4:offset + 8]
            chunk_data = frame[offset + 8:offset + 8 + length]
            offset += 12 + length
            if chunk_type == b"IHDR" and len(chunk_data) >= 13:
                width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
            elif chunk_type == b"IDAT":
                idat.extend(chunk_data)
            elif chunk_type == b"IEND":
                break
        if not width or not height or bit_depth != 8 or color_type not in {0, 2, 4, 6} or not idat:
            return {
                "available": True,
                "width": width or 0,
                "height": height or 0,
                "error": "unsupported png layout",
                "findings": ["Screenshot PNG layout could not be analyzed."],
            }
        channels = {0: 1, 2: 3, 4: 2, 6: 4}[int(color_type)]
        stride = int(width) * channels
        decompressor = zlib.decompressobj()
        raw = decompressor.decompress(bytes(idat), (stride + 1) * int(height) + 1024)
        previous = bytearray(stride)
        total = bright = dark = saturated = sampled = 0
        step_x = max(1, int(width) // 96)
        step_y = max(1, int(height) // 72)
        pos = 0
        for y in range(int(height)):
            if pos >= len(raw):
                break
            filter_type = raw[pos]
            pos += 1
            row = bytearray(raw[pos:pos + stride])
            pos += stride
            for i in range(stride):
                left = row[i - channels] if i >= channels else 0
                up = previous[i] if i < len(previous) else 0
                up_left = previous[i - channels] if i >= channels and i - channels < len(previous) else 0
                if filter_type == 1:
                    row[i] = (row[i] + left) & 0xFF
                elif filter_type == 2:
                    row[i] = (row[i] + up) & 0xFF
                elif filter_type == 3:
                    row[i] = (row[i] + ((left + up) // 2)) & 0xFF
                elif filter_type == 4:
                    row[i] = (row[i] + _paeth_predictor(left, up, up_left)) & 0xFF
            if y % step_y == 0:
                for x in range(0, int(width), step_x):
                    base = x * channels
                    if color_type == 0:
                        r = g = b = row[base]
                    elif color_type == 4:
                        r = g = b = row[base]
                    else:
                        r, g, b = row[base], row[base + 1], row[base + 2]
                    lum = (int(r) * 299 + int(g) * 587 + int(b) * 114) // 1000
                    total += lum
                    sampled += 1
                    if lum >= 245:
                        bright += 1
                    if lum <= 16:
                        dark += 1
                    if max(r, g, b) - min(r, g, b) >= 48:
                        saturated += 1
            previous = row
        avg_luma = round(total / sampled, 2) if sampled else 0
        bright_ratio = round(bright / sampled, 4) if sampled else 0
        dark_ratio = round(dark / sampled, 4) if sampled else 0
        color_ratio = round(saturated / sampled, 4) if sampled else 0
        findings: list[str] = []
        if sampled <= 0:
            findings.append("Screenshot could not be sampled for visual evidence.")
        elif bright_ratio >= 0.985:
            findings.append("Screenshot appears almost entirely white or blank.")
        elif dark_ratio >= 0.985:
            findings.append("Screenshot appears almost entirely dark or blank.")
        elif color_ratio < 0.002 and bright_ratio > 0.92:
            findings.append("Screenshot has very low visual variation and may be an empty shell.")
        return {
            "available": True,
            "width": int(width),
            "height": int(height),
            "sampled_pixels": sampled,
            "avg_luma": avg_luma,
            "bright_ratio": bright_ratio,
            "dark_ratio": dark_ratio,
            "color_variation_ratio": color_ratio,
            "findings": findings,
        }
    except Exception as exc:
        return {"available": True, "error": str(exc), "findings": ["Screenshot visual analysis failed."]}


def _normalize_permission_mode(mode: str | None) -> str:
    value = str(mode or "").strip().lower()
    return value if value in _BROWSER_PERMISSION_MODES else "none"


def _normalize_approval_mode(mode: Any = None) -> str:
    if isinstance(mode, bool):
        return "off" if mode is False else "manual"
    value = str(mode or "").strip().lower()
    if value in {"ask", "manual"}:
        return "manual"
    if value in {"deny", "smart"}:
        return "smart"
    if value in {"yolo", "off"}:
        return "off"
    return value if value in {"manual", "smart", "off"} else "manual"


def _current_approval_mode() -> str:
    try:
        from cli.config import load_config as _load_cli_config

        cfg = _load_cli_config()
        approvals = cfg.get("approvals", {}) if isinstance(cfg, dict) else {}
        if not isinstance(approvals, dict):
            approvals = {}
        return _normalize_approval_mode(approvals.get("mode", "manual"))
    except Exception:
        return "manual"


def _permission_record_is_expired(record: dict[str, Any] | None) -> bool:
    if not record:
        return False
    try:
        expires_at = float(record.get("expires_at") or 0)
    except (TypeError, ValueError):
        expires_at = 0.0
    if expires_at > 0:
        return time.time() > expires_at
    try:
        updated_at = float(record.get("updated_at") or 0)
    except (TypeError, ValueError):
        updated_at = 0.0
    return bool(updated_at and (time.time() - updated_at) > _PERMISSION_TTL_SECONDS)


def _permission_record_for_session(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    with _PERMISSIONS_LOCK:
        current = dict(_PERMISSIONS.get(sid) or {}) if sid else {}
        if current and _permission_record_is_expired(current):
            _PERMISSIONS.pop(sid, None)
            current = {}
        if not current:
            global_permission = dict(_GLOBAL_PERMISSION)
            if global_permission and _permission_record_is_expired(global_permission):
                _GLOBAL_PERMISSION.clear()
                global_permission = {}
            current = global_permission
    return current


def _forget_session_permission(session_id: str) -> None:
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _PERMISSIONS_LOCK:
        _PERMISSIONS.pop(sid, None)


def _active_goal_context(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"available": True, "present": False, "active": False, "session_id": ""}
    try:
        from .goals import goal_command_payload

        payload = goal_command_payload(sid, "status")
        goal = payload.get("goal") if isinstance(payload, dict) else None
        if not isinstance(goal, dict) or not str(goal.get("goal") or "").strip():
            return {
                "available": True,
                "present": False,
                "active": False,
                "session_id": sid,
                "message": str(payload.get("message") or "") if isinstance(payload, dict) else "",
            }
        status = str(goal.get("status") or "").strip().lower()
        return {
            "available": True,
            "present": True,
            "active": status == "active",
            "goal": str(goal.get("goal") or ""),
            "status": status,
            "turns_used": int(goal.get("turns_used") or 0),
            "max_turns": int(goal.get("max_turns") or 0),
            "last_verdict": goal.get("last_verdict"),
            "last_reason": goal.get("last_reason"),
            "paused_reason": goal.get("paused_reason"),
            "session_id": str(goal.get("session_id") or sid),
            "message": str(payload.get("message") or "") if isinstance(payload, dict) else "",
        }
    except Exception as exc:
        return {
            "available": False,
            "present": False,
            "active": False,
            "session_id": sid,
            "error": str(exc),
        }


def browser_permission_status(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"session_id": "", "mode": "none", "granted": False}
    current = _permission_record_for_session(sid)
    mode = _normalize_permission_mode(current.get("mode"))
    can_watch = mode in {"read", "control"}
    can_control = mode == "control"
    return {
        "session_id": sid,
        "mode": mode,
        "granted": mode != "none",
        "updated_at": current.get("updated_at"),
        "expires_at": current.get("expires_at"),
        "source_session_id": str(current.get("granted_session_id") or current.get("session_id") or sid),
        "can_watch": can_watch,
        "can_control": can_control,
        "needs_user_approval": not can_watch,
    }


def browser_permission_grant(session_id: str, mode: str = "control") -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    normalized = _normalize_permission_mode(mode)
    if normalized == "none":
        raise ValueError("permission mode must be read or control")
    token = secrets.token_urlsafe(24)
    now = time.time()
    record = {
        "mode": normalized,
        "token": token,
        "updated_at": now,
        "expires_at": now + _PERMISSION_TTL_SECONDS,
        "granted_session_id": sid,
    }
    with _PERMISSIONS_LOCK:
        _GLOBAL_PERMISSION.clear()
        _GLOBAL_PERMISSION.update(record)
        _PERMISSIONS.clear()
        _PERMISSIONS[sid] = dict(record)
    return browser_permission_status(sid)


def browser_permission_revoke(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"session_id": "", "mode": "none", "granted": False}
    with _PERMISSIONS_LOCK:
        _GLOBAL_PERMISSION.clear()
        _PERMISSIONS.clear()
    return browser_permission_status(sid)


def browser_permission_token(session_id: str) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    record = _permission_record_for_session(sid)
    return str(record.get("token") or "")


def browser_permission_token_valid(session_id: str, token: str | None, required: str) -> bool:
    sid = str(session_id or "").strip()
    supplied = str(token or "").strip()
    if not sid or not supplied or not _has_permission(sid, required):
        return False
    expected = browser_permission_token(sid)
    return bool(expected and secrets.compare_digest(expected, supplied))

def _permission_required_payload(session_id: str, required: str) -> dict[str, Any]:
    permission = browser_permission_status(session_id)
    approval_mode = _current_approval_mode()
    active_goal = _active_goal_context(session_id)
    current_mode = str(permission.get("mode") or "none")
    required_mode = "read" if required == "read" else "control"
    if required_mode == "read":
        suggested_action = "request_read_permission"
        permission_steps = ["enable_browser_watch"] if current_mode == "none" else []
    else:
        suggested_action = "request_control_permission"
        permission_steps = []
        if current_mode == "none":
            permission_steps.append("enable_browser_watch")
        if current_mode != "control":
            permission_steps.append("enable_browser_control")
    return {
        "ok": False,
        "code": "browser_permission_required",
        "error": "Browser permission is required for this action.",
        "required_mode": required_mode,
        "suggested_action": suggested_action,
        "permission_steps": permission_steps,
        "permission_step_labels": [_permission_step_label(step) for step in permission_steps],
        "permission": permission,
        "approval_mode": approval_mode,
        "approval_modes": ["manual", "smart", "off"],
        "active_goal": active_goal,
    }


def _permission_step_label(step: str) -> str:
    value = str(step or "").strip()
    labels = {
        "enable_browser_watch": "Enable browser watch",
        "enable_browser_control": "Enable browser control",
        "request_read_permission": "Enable browser watch",
        "request_control_permission": "Enable browser control",
    }
    return labels.get(value, value.replace("_", " "))


def _has_permission(session_id: str, required: str) -> bool:
    mode = browser_permission_status(session_id).get("mode")
    if required == "read":
        return mode in {"read", "control"}
    if required == "control":
        return mode == "control"
    return False


def _action_detail_text(action: str, payload: dict[str, Any] | None = None, *, step_index: int | None = None, step_total: int | None = None) -> str:
    payload = payload or {}
    act = str(action or "").strip().lower()
    ref = str(payload.get("ref") or "").strip()
    selector = str(payload.get("selector") or "").strip()
    label = ""
    if act == "click":
        label = ref or selector or "click"
    elif act == "type":
        label = ref or selector or "type"
    elif act in {"navigate", "open"}:
        label = str(payload.get("url") or "").strip() or "navigate"
    elif act == "wait":
        if selector:
            label = selector
        elif payload.get("until"):
            label = str(payload.get("until") or "wait")
        else:
            ms = payload.get("ms", payload.get("milliseconds"))
            label = f"{int(float(ms))}ms" if ms is not None and str(ms).strip() else "wait"
    elif act == "press":
        label = str(payload.get("key") or "Enter")
    elif act in {"scroll", "back", "forward", "reload", "stop", "move", "snapshot", "state", "action_v1", "browser_action_v1", "sequence"}:
        label = ""
    else:
        label = ref or selector or str(payload.get("action") or payload.get("kind") or "").strip()
    prefix = ""
    if step_index and step_total and step_total > 1:
        prefix = f"step {step_index}/{step_total}: "
    return (prefix + act + (f" {label}" if label else "")).strip()


def _url_has_secret_material(url: str) -> bool:
    try:
        decoded = urlparse(url).geturl()
    except Exception:
        decoded = str(url or "")
    from urllib.parse import unquote
    decoded = unquote(decoded)
    return bool(_SECRET_PATTERN.search(str(url or "")) or _SECRET_PATTERN.search(decoded))


def _permission_required_for_action(action: str) -> str:
    act = str(action or "").strip().lower()
    return "read" if act in _READ_ACTIONS else "control"


def _safe_getenv(name: str) -> str:
    return str(os.environ.get(name, "") or "").strip()


def _split_host_port(value: str) -> tuple[str, int | None]:
    raw = str(value or "").strip()
    if not raw:
        return "", None
    if raw.startswith("[") and "]" in raw:
        host, _, rest = raw[1:].partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            return host.lower(), int(rest[1:])
        return host.lower(), None
    if raw.count(":") == 1:
        host, port_s = raw.rsplit(":", 1)
        if port_s.isdigit():
            return host.lower(), int(port_s)
    return raw.lower(), None


def _wildcard_match(host: str, pattern: str) -> bool:
    host = host.lower().strip()
    pattern = pattern.lower().strip()
    if not host or not pattern:
        return False
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]
        return host.endswith(suffix) or host == suffix[1:]
    return host == pattern


def _effective_port(scheme: str, port: int | None) -> int | None:
    if port is not None:
        return int(port)
    scheme = (scheme or "").lower()
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _env_allowed_hosts() -> list[str]:
    raw = _safe_getenv("SIDEKICK_BROWSER_ALLOW_HOSTS")
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[,\s]+", raw) if p.strip()]
    return parts


def _env_require_allowlist() -> bool:
    raw = _safe_getenv("SIDEKICK_BROWSER_REQUIRE_ALLOWLIST")
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_local_host(host: str) -> bool:
    host = str(host or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1", "ip6-localhost", "ip6-loopback"}:
        return True
    if host.endswith(".localhost") or host.endswith(".local") or host.endswith(".lan") or host.endswith(".internal"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local)


def _is_allowed_browser_target(url: str, *, origin_host: str | None = None) -> tuple[bool, str]:
    raw = str(url or "").strip()
    if not raw:
        return False, "URL is required"
    if raw == "about:blank":
        return True, ""
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in {"http", "https"}:
        return False, "Only http and https URLs are allowed"
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return False, "URL is missing a hostname"

    origin_host = str(origin_host or "").strip().lower()
    origin_name, origin_port = _split_host_port(origin_host)
    target_port = _effective_port(scheme, parsed.port)
    if origin_name and host == origin_name and (_effective_port(scheme, origin_port) in {None, target_port} or target_port is None):
        return True, ""
    if _is_local_host(host):
        return True, ""
    for pattern in _env_allowed_hosts():
        if _wildcard_match(host, pattern):
            return True, ""
    for pattern in _DEFAULT_ALLOWED_HOST_PATTERNS:
        if _wildcard_match(host, pattern):
            return True, ""
    if _env_require_allowlist():
        return False, "Target host is not allowlisted"
    return True, ""


def normalize_browser_url(url: str, base_url: str | None = None) -> str:
    raw = str(url or "").strip()
    if not raw:
        return ""
    if raw.startswith("//"):
        return "https:" + raw
    if re.match(r"^(localhost|127(?:\.\d{1,3}){3}|\[?::1\]?)(:\d+)?([/?#]|$)", raw, re.IGNORECASE):
        return "http://" + raw
    if re.match(r"^[a-z0-9.-]+\.[a-z]{2,}(:\d+)?([/?#]|$)", raw, re.IGNORECASE):
        return "https://" + raw
    parsed = urlsplit(raw)
    if parsed.scheme:
        return urlunsplit(parsed)
    if base_url:
        joined = urljoin(base_url, raw)
        return joined
    return ""


def _blank_png_bytes() -> bytes:
    return base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAwAAAAAACAIAAABv1vWqAAAACXBIWXMAAAsTAAALEwEAmpwY"
        "AAAAB3RJTUUH5QgNEQ4jXhN0dQAAAB1pVFh0Q29tbWVudAAAAAAAVGVzdCBibGFuayBwbmcu"
        "AAAAPklEQVR42u3BAQ0AAADCoPdPbQ43oAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        "AAAAAAAAAAAAAAB4GQYAAWw5aWMAAAAASUVORK5CYII="
    )


@dataclass
class BrowserSnapshot:
    session_id: str
    status: str = "idle"
    url: str = "about:blank"
    title: str = ""
    error: str = ""
    allowed: bool = True
    allowed_reason: str = ""
    can_go_back: bool = False
    can_go_forward: bool = False
    busy: bool = False
    frame_rev: int = 0
    viewport_width: int = _DEFAULT_VIEWPORT["width"]
    viewport_height: int = _DEFAULT_VIEWPORT["height"]
    cursor_x: float = 0.0
    cursor_y: float = 0.0
    click_x: float | None = None
    click_y: float | None = None
    click_kind: str | None = None
    click_ts: float | None = None
    last_action: str = ""
    last_action_detail: str = ""
    frame_url: str = ""
    ready_state: str = ""
    scroll_x: float = 0.0
    scroll_y: float = 0.0
    viewport_inner_width: int = 0
    viewport_inner_height: int = 0
    active_element: str = ""
    active_element_label: str = ""
    target_x: float | None = None
    target_y: float | None = None
    target_width: float | None = None
    target_height: float | None = None
    target_label: str = ""
    target_selector: str = ""
    target_kind: str = ""
    target_visible: bool = False
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        data = dataclasses.asdict(self)
        data["frame_url"] = self.frame_url or f"/api/browser/frame?session_id={self.session_id}&rev={self.frame_rev}"
        return data


class BrowserSession:
    def __init__(self, manager: "BrowserManager", session_id: str):
        self.manager = manager
        self.session_id = session_id
        self._lock = asyncio.Lock()
        self._subscribers_lock = threading.Lock()
        self._subscribers: list[queue.Queue] = []
        self._page = None
        self._context = None
        self._snapshot = BrowserSnapshot(session_id=session_id)
        self._frame_bytes = _blank_png_bytes()
        self._frame_mime = "image/png"
        self._last_access_at = time.time()
        self._created = False
        self._page_error = ""
        self._agent_refs: dict[str, dict[str, Any]] = {}
        self._console_events: list[dict[str, Any]] = []
        self._network_events: list[dict[str, Any]] = []

    def _touch(self) -> None:
        self._last_access_at = time.time()

    def _record_browser_event(self, target: list[dict[str, Any]], event: dict[str, Any]) -> None:
        event["ts"] = time.time()
        target.append(event)
        del target[:-60]

    async def ensure(self) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            await self._capture_state("ready", capture_frame=True)
            return self._snapshot

    async def _ensure_created_locked(self) -> None:
        if self._created and self._page and self._context:
            return
        await self._create_browser()

    async def _create_browser(self) -> None:
        browser = await self.manager.browser()
        self._context = await browser.new_context(
            viewport=dict(_DEFAULT_VIEWPORT),
            ignore_https_errors=True,
            color_scheme="light",
            reduced_motion="reduce",
        )
        self._page = await self._context.new_page()
        self._page.set_default_timeout(15000)
        self._page.set_default_navigation_timeout(15000)
        self._wire_page_events(self._page)
        await self._page.goto("about:blank")
        self._created = True

    def _wire_page_events(self, page) -> None:
        async def _on_nav(_frame):
            try:
                await self._capture_state("navigated", capture_frame=True)
            except Exception:
                logger.exception("browser frame capture after navigation failed")

        async def _on_load():
            try:
                await self._capture_state("load", capture_frame=True)
            except Exception:
                logger.exception("browser frame capture after load failed")

        def _schedule_nav(frame):
            asyncio.create_task(_on_nav(frame))

        def _schedule_load():
            asyncio.create_task(_on_load())

        def _page_error(exc):
            self._page_error = str(exc)
            self._record_browser_event(self._console_events, {
                "kind": "pageerror",
                "type": "error",
                "text": str(exc),
            })
            asyncio.create_task(self._capture_state("pageerror", capture_frame=False))

        def _console(msg):
            try:
                text = msg.text
                if callable(text):
                    text = text()
            except Exception:
                text = ""
            try:
                msg_type = msg.type
                if callable(msg_type):
                    msg_type = msg_type()
            except Exception:
                msg_type = "log"
            self._record_browser_event(self._console_events, {
                "kind": "console",
                "type": str(msg_type or "log"),
                "text": str(text or "")[:1200],
            })

        def _request_failed(req):
            try:
                failure = req.failure
                if callable(failure):
                    failure = failure()
            except Exception:
                failure = ""
            try:
                method = req.method
            except Exception:
                method = ""
            try:
                url = req.url
            except Exception:
                url = ""
            self._record_browser_event(self._network_events, {
                "kind": "requestfailed",
                "method": str(method or ""),
                "url": str(url or "")[:1200],
                "error": str(failure or "request failed")[:800],
            })

        def _response(resp):
            try:
                status = int(resp.status)
            except Exception:
                status = 0
            if status < 400:
                return
            try:
                req = resp.request
                method = req.method
            except Exception:
                method = ""
            try:
                url = resp.url
            except Exception:
                url = ""
            self._record_browser_event(self._network_events, {
                "kind": "response",
                "method": str(method or ""),
                "url": str(url or "")[:1200],
                "status": status,
            })

        page.on("framenavigated", _schedule_nav)
        page.on("load", _schedule_load)
        page.on("pageerror", _page_error)
        page.on("console", _console)
        page.on("requestfailed", _request_failed)
        page.on("response", _response)

    async def reset(self) -> BrowserSnapshot:
        async with self._lock:
            await self._close_inner()
            await self._create_browser()
            await self._capture_state("reset", capture_frame=True)
            self._notify()
            return self._snapshot

    async def close(self) -> None:
        async with self._lock:
            await self._close_inner()
            self._snapshot.status = "idle"
            self._snapshot.busy = False
            self._snapshot.last_action = "closed"
            self._notify()

    async def _close_inner(self) -> None:
        self._created = False
        try:
            if self._page:
                await self._page.close()
        except Exception:
            pass
        try:
            if self._context:
                await self._context.close()
        except Exception:
            pass
        self._page = None
        self._context = None
        self._frame_bytes = _blank_png_bytes()
        self._snapshot = BrowserSnapshot(session_id=self.session_id)
        self._page_error = ""
        self._console_events = []
        self._network_events = []

    def _reset_page_events(self) -> None:
        self._page_error = ""
        self._console_events = []
        self._network_events = []

    async def snapshot(self) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            return self._snapshot

    async def diagnostics(self) -> dict[str, Any]:
        async with self._lock:
            self._touch()
            console_recent = list(self._console_events[-20:])
            network_recent = list(self._network_events[-20:])
            console_findings = [
                ev for ev in console_recent
                if str(ev.get("type") or "").lower() in {"error", "warning"} or str(ev.get("kind") or "") == "pageerror"
            ]
            network_findings = [
                ev for ev in network_recent
                if int(ev.get("status") or 0) >= 400 or ev.get("kind") == "requestfailed"
            ]
            return {
                "available": True,
                "console_events": console_recent,
                "network_events": network_recent,
                "console_findings": console_findings,
                "network_findings": network_findings,
                "console_error_count": len(console_findings),
                "network_error_count": len(network_findings),
                "page_error": self._page_error,
            }

    def _update_snapshot(self, **kwargs: Any) -> BrowserSnapshot:
        for key, value in kwargs.items():
            setattr(self._snapshot, key, value)
        self._snapshot.updated_at = time.time()
        self._snapshot.frame_url = f"/api/browser/frame?session_id={self.session_id}&rev={self._snapshot.frame_rev}"
        return self._snapshot

    def _action_payload(self, action: str, payload: dict[str, Any] | None = None, *, step_index: int | None = None, step_total: int | None = None) -> str:
        return _action_detail_text(action, payload, step_index=step_index, step_total=step_total)

    def _expected_frame_rev(self, payload: dict[str, Any] | None = None) -> int | None:
        payload = payload or {}
        for key in ("expected_frame_rev", "observed_frame_rev"):
            if key not in payload:
                continue
            raw = payload.get(key)
            if raw is None or str(raw).strip() == "":
                continue
            try:
                return int(raw)
            except (TypeError, ValueError):
                return -1
        return None

    def _frame_rev_guard(self, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        expected = self._expected_frame_rev(payload)
        if expected is None:
            return None
        current = int(self._snapshot.frame_rev or 0)
        if expected == current:
            return None
        state = self._snapshot.to_dict()
        sid = str(getattr(self, "session_id", "") or state.get("session_id") or "")
        approval_mode = _current_approval_mode()
        active_goal = _active_goal_context(sid)
        return {
            "ok": False,
            "code": "browser_frame_stale",
            "error": f"Browser frame changed since inspection: expected rev {expected}, current rev {current}. Refresh snapshot and retry.",
            "expected_frame_rev": expected,
            "current_frame_rev": current,
            "approval_mode": approval_mode,
            "active_goal": active_goal,
            "state": state,
        }

    def _resolve_ref_target(self, ref: str) -> dict[str, Any] | None:
        return self._agent_refs.get(str(ref or "").strip())

    async def _resolve_element_target(self, selector: str, *, kind: str = "", fallback_label: str = "") -> dict[str, Any] | None:
        selector = str(selector or "").strip()
        if not selector or not self._page:
            return None
        try:
            locator = self._page.locator(selector).first
            box = await locator.bounding_box()
            label = ""
            try:
                label = await locator.evaluate(
                    """(el) => String(
                      el.getAttribute('aria-label') ||
                      el.getAttribute('title') ||
                      el.getAttribute('placeholder') ||
                      el.innerText ||
                      el.value ||
                      el.textContent ||
                      ''
                    ).trim().replace(/\\s+/g, ' ')"""
                )
            except Exception:
                label = ""
            if not label:
                label = fallback_label or selector
            if not box:
                return {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 0.0,
                    "height": 0.0,
                    "label": label,
                    "selector": selector,
                    "kind": kind,
                    "visible": False,
                }
            return {
                "x": float(box.get("x") or 0.0),
                "y": float(box.get("y") or 0.0),
                "width": float(box.get("width") or 0.0),
                "height": float(box.get("height") or 0.0),
                "label": label,
                "selector": selector,
                "kind": kind,
                "visible": True,
            }
        except Exception:
            return None

    def _set_action_target(self, target: dict[str, Any] | None) -> None:
        if not target:
            self._update_snapshot(
                target_x=None,
                target_y=None,
                target_width=None,
                target_height=None,
                target_label="",
                target_selector="",
                target_kind="",
                target_visible=False,
            )
            return
        self._update_snapshot(
            target_x=target.get("x"),
            target_y=target.get("y"),
            target_width=target.get("width"),
            target_height=target.get("height"),
            target_label=str(target.get("label") or ""),
            target_selector=str(target.get("selector") or ""),
            target_kind=str(target.get("kind") or ""),
            target_visible=bool(target.get("visible")),
        )

    def _notify(self) -> None:
        payload = {"type": "snapshot", "state": self._snapshot.to_dict()}
        stale: list[queue.Queue] = []
        with self._subscribers_lock:
            for q in list(self._subscribers):
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    stale.append(q)
            for q in stale:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass

    def subscribe(self) -> tuple[queue.Queue, BrowserSnapshot]:
        q = queue.Queue(maxsize=16)
        with self._subscribers_lock:
            if len(self._subscribers) >= _MAX_SUBSCRIBERS_PER_SESSION:
                self._subscribers.pop(0)
            self._subscribers.append(q)
        q.put_nowait({"type": "initial", "state": self._snapshot.to_dict()})
        return q, self._snapshot

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._subscribers_lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    async def _capture_state(self, action: str, *, capture_frame: bool) -> BrowserSnapshot:
        if not self._page:
            return self._snapshot
        self._touch()
        status = "idle"
        error = ""
        url = self._snapshot.url
        title = self._snapshot.title
        can_go_back = False
        can_go_forward = False
        ready_state = self._snapshot.ready_state
        scroll_x = self._snapshot.scroll_x
        scroll_y = self._snapshot.scroll_y
        viewport_inner_width = self._snapshot.viewport_inner_width
        viewport_inner_height = self._snapshot.viewport_inner_height
        active_element = self._snapshot.active_element
        active_element_label = self._snapshot.active_element_label
        target_x = self._snapshot.target_x
        target_y = self._snapshot.target_y
        target_width = self._snapshot.target_width
        target_height = self._snapshot.target_height
        target_label = self._snapshot.target_label
        target_selector = self._snapshot.target_selector
        target_kind = self._snapshot.target_kind
        target_visible = self._snapshot.target_visible
        try:
            url = self._page.url or url
            title = await self._page.title()
        except Exception:
            pass
        allowed, reason = _is_allowed_browser_target(url, origin_host=None)
        if not allowed:
            try:
                await self._page.goto("about:blank", wait_until="domcontentloaded")
                url = "about:blank"
                title = ""
            except Exception:
                pass
            status = "blocked"
            error = reason or "Blocked"
        elif _url_has_secret_material(url):
            try:
                await self._page.goto("about:blank", wait_until="domcontentloaded")
                url = "about:blank"
                title = ""
            except Exception:
                pass
            status = "blocked"
            error = "Blocked: URL appears to contain secret material"
        try:
            can_go_back = await self._page.evaluate("() => !!(window.history && window.history.length > 1)")
        except Exception:
            can_go_back = False
        try:
            can_go_forward = await self._page.evaluate("() => !!(window.history && window.history.state !== null)")
        except Exception:
            can_go_forward = False
        try:
            page_meta = await self._page.evaluate(
                """() => {
                  const el = document.activeElement;
                  const label = (node) => {
                    if (!node) return '';
                    const tag = node.tagName ? node.tagName.toLowerCase() : '';
                    if (tag === 'body' || tag === 'html') return '';
                    return String(
                      node.getAttribute('aria-label') ||
                      node.getAttribute('title') ||
                      node.getAttribute('placeholder') ||
                      node.innerText ||
                      node.value ||
                      node.textContent ||
                      ''
                    ).trim().replace(/\\s+/g, ' ').slice(0, 160);
                  };
                  const selectorFor = (node) => {
                    if (!node) return '';
                    if (node.id) return '#' + CSS.escape(node.id);
                    const testId = node.getAttribute('data-testid');
                    if (testId) return '[data-testid="' + CSS.escape(testId) + '"]';
                    const tag = node.tagName ? node.tagName.toLowerCase() : '';
                    const name = node.getAttribute ? node.getAttribute('name') : '';
                    if (tag && name) return tag + '[name="' + CSS.escape(name) + '"]';
                    return tag || '';
                  };
                  return {
                    readyState: document.readyState || '',
                    scrollX: window.scrollX || 0,
                    scrollY: window.scrollY || 0,
                    viewportInnerWidth: window.innerWidth || 0,
                    viewportInnerHeight: window.innerHeight || 0,
                    activeElement: selectorFor(el),
                    activeElementLabel: label(el),
                  };
                }"""
            )
            ready_state = str((page_meta or {}).get("readyState") or ready_state)
            scroll_x = float((page_meta or {}).get("scrollX") or scroll_x or 0)
            scroll_y = float((page_meta or {}).get("scrollY") or scroll_y or 0)
            viewport_inner_width = int((page_meta or {}).get("viewportInnerWidth") or viewport_inner_width or 0)
            viewport_inner_height = int((page_meta or {}).get("viewportInnerHeight") or viewport_inner_height or 0)
            active_element = str((page_meta or {}).get("activeElement") or "")
            active_element_label = str((page_meta or {}).get("activeElementLabel") or "")
        except Exception:
            pass
        if self._page_error:
            status = "error"
            error = self._page_error
        self._update_snapshot(
            status=status,
            url=url or "about:blank",
            title=title or "",
            error=error,
            busy=False,
            can_go_back=bool(can_go_back),
            can_go_forward=bool(can_go_forward),
            last_action=action,
            ready_state=ready_state,
            scroll_x=scroll_x,
            scroll_y=scroll_y,
            viewport_inner_width=viewport_inner_width,
            viewport_inner_height=viewport_inner_height,
            active_element=active_element,
            active_element_label=active_element_label,
            target_x=target_x if action in {"click", "type"} else None,
            target_y=target_y if action in {"click", "type"} else None,
            target_width=target_width if action in {"click", "type"} else None,
            target_height=target_height if action in {"click", "type"} else None,
            target_label=target_label if action in {"click", "type"} else "",
            target_selector=target_selector if action in {"click", "type"} else "",
            target_kind=target_kind if action in {"click", "type"} else "",
            target_visible=bool(target_visible) if action in {"click", "type"} else False,
        )
        if capture_frame:
            try:
                try:
                    await self._page.wait_for_function(
                        "() => document.body && document.body.innerText && document.body.innerText.trim().length > 0",
                        timeout=2500,
                    )
                except Exception:
                    pass
                try:
                    await self._page.evaluate(
                        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                    )
                except Exception:
                    await self._page.wait_for_timeout(250)
                self._frame_bytes = await self._page.screenshot(
                    type="png",
                    animations="disabled",
                    caret="hide",
                    omit_background=False,
                    scale="device",
                )
                self._snapshot.frame_rev += 1
                self._snapshot.frame_url = f"/api/browser/frame?session_id={self.session_id}&rev={self._snapshot.frame_rev}"
            except Exception as exc:
                self._frame_bytes = _blank_png_bytes()
                self._update_snapshot(status="error", error=str(exc), busy=False)
        self._notify()
        return self._snapshot

    async def _capture_after_navigation_timeout(self, action: str, *, target_url: str | None = None) -> BrowserSnapshot:
        """Capture the real visible page after Playwright reports a navigation timeout.

        Some pages keep network/navigation bookkeeping open longer than their rendered
        document needs. Freezing the timeout snapshot as an error makes the WebUI and
        agent think the browser is broken even when the page is already usable.
        """
        deadline = time.monotonic() + 4.0
        target = str(target_url or "").strip()
        while time.monotonic() < deadline:
            try:
                current_url = str(self._page.url if self._page else "").strip()
                if current_url and current_url != "about:blank":
                    break
                if self._page:
                    ready_state = ""
                    try:
                        ready_state = str(await self._page.evaluate("() => document.readyState") or "")
                    except Exception:
                        ready_state = ""
                    if ready_state in {"interactive", "complete"} and (not target or current_url == target):
                        break
            except Exception:
                pass
            await asyncio.sleep(0.2)
        return await self._capture_state(action, capture_frame=True)

    async def navigate(self, url: str, *, origin_host: str | None = None) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            assert self._page is not None
            target = normalize_browser_url(url, self._snapshot.url)
            allowed, reason = _is_allowed_browser_target(target, origin_host=origin_host)
            if allowed and _url_has_secret_material(target):
                allowed = False
                reason = "Blocked: URL appears to contain secret material"
            detail = self._action_payload("navigate", {"url": target})
            self._update_snapshot(
                status="running",
                busy=True,
                last_action="navigate",
                last_action_detail=detail,
                allowed=allowed,
                allowed_reason=reason,
                error="",
            )
            self._notify()
            if not allowed:
                self._update_snapshot(status="blocked", busy=False, error=reason or "Blocked", allowed=False, allowed_reason=reason)
                self._notify()
                return self._snapshot
            try:
                self._reset_page_events()
                await self._page.goto(target, wait_until="domcontentloaded")
            except Exception as exc:
                if "Timeout" in str(exc):
                    return await self._capture_after_navigation_timeout("navigate", target_url=target)
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("navigate", capture_frame=True)

    async def back(self) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            self._update_snapshot(status="running", busy=True, last_action="back", last_action_detail=self._action_payload("back"), error="")
            self._notify()
            try:
                self._reset_page_events()
                await self._page.go_back(wait_until="domcontentloaded")
            except Exception as exc:
                if "Timeout" in str(exc):
                    return await self._capture_after_navigation_timeout("back")
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("back", capture_frame=True)

    async def forward(self) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            self._update_snapshot(status="running", busy=True, last_action="forward", last_action_detail=self._action_payload("forward"), error="")
            self._notify()
            try:
                self._reset_page_events()
                await self._page.go_forward(wait_until="domcontentloaded")
            except Exception as exc:
                if "Timeout" in str(exc):
                    return await self._capture_after_navigation_timeout("forward")
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("forward", capture_frame=True)

    async def reload(self) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            self._update_snapshot(status="running", busy=True, last_action="reload", last_action_detail=self._action_payload("reload"), error="")
            self._notify()
            try:
                self._reset_page_events()
                await self._page.reload(wait_until="domcontentloaded")
            except Exception as exc:
                if "Timeout" in str(exc):
                    return await self._capture_after_navigation_timeout("reload")
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("reload", capture_frame=True)

    async def stop(self) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            if self._page:
                try:
                    await self._page.evaluate("() => { try { window.stop(); } catch (_) {} }")
                except Exception:
                    pass
            self._update_snapshot(status="idle", busy=False, last_action="stop", last_action_detail=self._action_payload("stop"))
            self._notify()
            return self._snapshot

    async def move(self, x: float, y: float) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            self._update_snapshot(
                cursor_x=float(x),
                cursor_y=float(y),
                last_action="move",
                last_action_detail=self._action_payload("move", {"x": x, "y": y}),
            )
            try:
                await self._page.mouse.move(float(x), float(y), steps=6)
            except Exception as exc:
                self._update_snapshot(status="error", error=str(exc))
            self._notify()
            return self._snapshot

    async def click(self, x: float, y: float, button: str = "left") -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            self._set_action_target({
                "x": float(x) - 12,
                "y": float(y) - 12,
                "width": 24,
                "height": 24,
                "label": f"{button} click",
                "selector": "",
                "kind": "click",
                "visible": True,
            })
            self._update_snapshot(
                status="running",
                busy=True,
                cursor_x=float(x),
                cursor_y=float(y),
                click_x=float(x),
                click_y=float(y),
                click_kind=button,
                click_ts=time.time(),
                last_action="click",
                last_action_detail=self._action_payload("click", {"button": button}),
                error="",
            )
            self._notify()
            try:
                await self._page.mouse.move(float(x), float(y), steps=10)
                await self._page.mouse.click(float(x), float(y), button=button)
            except Exception as exc:
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("click", capture_frame=True)

    async def type_text(self, ref: str, text: str, *, selector: str | None = None) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            target = self._resolve_ref_target(ref) if ref else None
            selector = str(selector or (target.get("selector") if target else "") or "").strip()
            if not target:
                if not selector:
                    self._update_snapshot(status="error", busy=False, error=f"Unknown browser ref: {ref}")
                    self._notify()
                    return self._snapshot
            x = float(target.get("x") or 0) if target else 0.0
            y = float(target.get("y") or 0) if target else 0.0
            tag = str(target.get("tag") or "").strip().lower() if target else ""
            target_meta = None
            if selector:
                target_meta = await self._resolve_element_target(selector, kind="type", fallback_label=str(target.get("text") or target.get("label") or selector if target else selector))
            if not target_meta and target:
                target_meta = {
                    "x": float(target.get("x") or 0),
                    "y": float(target.get("y") or 0),
                    "width": float(target.get("w") or 24) or 24,
                    "height": float(target.get("h") or 24) or 24,
                    "label": str(target.get("text") or target.get("label") or ref or selector or "type"),
                    "selector": str(target.get("selector") or ""),
                    "kind": "type",
                    "visible": True,
                }
            self._set_action_target(target_meta)
            self._update_snapshot(status="running", busy=True, cursor_x=x, cursor_y=y, last_action="type", last_action_detail=self._action_payload("type", {"ref": ref, "selector": selector}), error="")
            self._notify()
            try:
                if selector and tag in {"input", "textarea", "contenteditable"}:
                    await self._page.locator(selector).fill(str(text or ""))
                elif selector:
                    await self._page.locator(selector).click()
                    try:
                        await self._page.keyboard.press("Control+A")
                    except Exception:
                        pass
                    await self._page.keyboard.type(str(text or ""))
                else:
                    await self._page.mouse.click(x, y)
                    try:
                        await self._page.keyboard.press("Control+A")
                    except Exception:
                        pass
                    await self._page.keyboard.type(str(text or ""))
            except Exception as exc:
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("type", capture_frame=True)

    async def _click_selector(self, selector: str, button: str = "left") -> BrowserSnapshot:
        selector = str(selector or "").strip()
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            if not selector:
                self._update_snapshot(status="error", busy=False, error="selector is required")
                self._notify()
                return self._snapshot
            target_meta = await self._resolve_element_target(selector, kind="click", fallback_label=selector)
            if not target_meta:
                target_meta = {
                    "x": 0.0,
                    "y": 0.0,
                    "width": 24,
                    "height": 24,
                    "label": selector,
                    "selector": selector,
                    "kind": "click",
                    "visible": True,
                }
            self._set_action_target(target_meta)
            self._update_snapshot(status="running", busy=True, last_action="click", last_action_detail=self._action_payload("click", {"selector": selector, "button": button}), error="")
            self._notify()
            try:
                await self._page.locator(selector).click(button=button)
            except Exception as exc:
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("click", capture_frame=True)

    async def press(self, key: str) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            self._update_snapshot(status="running", busy=True, last_action="press", last_action_detail=self._action_payload("press", {"key": key}), error="")
            self._notify()
            try:
                await self._page.keyboard.press(str(key or "Enter"))
            except Exception as exc:
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("press", capture_frame=True)

    async def scroll(self, direction: str = "down") -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            dy = -650 if str(direction or "").lower() in {"up", "north"} else 650
            self._update_snapshot(status="running", busy=True, last_action="scroll", last_action_detail=self._action_payload("scroll", {"direction": direction}), error="")
            self._notify()
            try:
                await self._page.mouse.wheel(0, dy)
            except Exception as exc:
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("scroll", capture_frame=True)

    async def wait(self, *, milliseconds: int = 1000, selector: str = "", state: str = "visible", until: str = "") -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            ms = max(int(milliseconds or 0), 0)
            detail = self._action_payload("wait", {"ms": ms, "selector": selector, "state": state, "until": until})
            self._update_snapshot(status="running", busy=True, last_action="wait", last_action_detail=detail, error="")
            self._notify()
            try:
                if selector:
                    await self._page.locator(selector).wait_for(state=str(state or "visible"), timeout=max(ms, 1000))
                elif until:
                    await self._page.wait_for_load_state(str(until or "load"), timeout=max(ms, 1000))
                else:
                    await self._page.wait_for_timeout(ms or 1000)
            except Exception as exc:
                self._update_snapshot(status="error", busy=False, error=str(exc))
                self._notify()
                return self._snapshot
            return await self._capture_state("wait", capture_frame=True)

    async def agent_snapshot(self, *, full: bool = False) -> dict[str, Any]:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            assert self._page is not None
            await self._capture_state("snapshot", capture_frame=True)
            try:
                data = await self._page.evaluate(
                    """(full) => {
                      const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                      };
                      const label = (el) => {
                        const tag = el.tagName ? el.tagName.toLowerCase() : '';
                        if (tag === 'body' || tag === 'html') return '';
                        return String(
                          el.getAttribute('aria-label') || el.getAttribute('title') ||
                          el.getAttribute('placeholder') || el.innerText || el.value || el.textContent || ''
                        ).trim().replace(/\\s+/g, ' ').slice(0, 160);
                      };
                      const selectorFor = (el) => {
                        if (el.id) return '#' + CSS.escape(el.id);
                        const testId = el.getAttribute('data-testid');
                        if (testId) return '[data-testid=\"' + CSS.escape(testId) + '\"]';
                        const tag = el.tagName.toLowerCase();
                        const name = el.getAttribute('name');
                        if (name) return tag + '[name=\"' + CSS.escape(name) + '\"]';
                        return tag;
                      };
                      const candidates = Array.from(document.querySelectorAll(
                        'a,button,input,textarea,select,[role=\"button\"],[role=\"link\"],[contenteditable=\"true\"]'
                      )).filter(visible).slice(0, 80).map((el, idx) => {
                        const r = el.getBoundingClientRect();
                        return {
                          ref: '@e' + (idx + 1),
                          tag: el.tagName.toLowerCase(),
                          role: el.getAttribute('role') || '',
                          text: label(el).slice(0, 160),
                          selector: selectorFor(el),
                          x: r.left + r.width / 2,
                          y: r.top + r.height / 2,
                          w: r.width,
                          h: r.height,
                        };
                      });
                      const bodyText = (document.body && document.body.innerText || '')
                        .replace(/\\n{3,}/g, '\\n\\n')
                        .trim()
                        .slice(0, full ? 12000 : 4000);
                      return {title: document.title || '', url: location.href, bodyText, elements: candidates};
                    }""",
                    bool(full),
                )
            except Exception as exc:
                return {"ok": False, "error": str(exc), "state": self._snapshot.to_dict()}
            self._agent_refs = {
                str(item.get("ref")): item
                for item in data.get("elements", [])
                if isinstance(item, dict) and item.get("ref")
            }
            lines = [f"URL: {data.get('url') or self._snapshot.url}", f"Title: {data.get('title') or self._snapshot.title}", ""]
            body_text = str(data.get("bodyText") or "").strip()
            if body_text:
                lines.extend(["Page text:", body_text, ""])
            if self._agent_refs:
                lines.append("Interactive elements:")
                for item in data.get("elements", []):
                    label = item.get("text") or item.get("role") or item.get("tag") or "element"
                    lines.append(f"{item.get('ref')} {item.get('tag')} {label}")
            ready_state = str(self._snapshot.ready_state or data.get("readyState") or "").strip()
            focus = str(self._snapshot.active_element_label or data.get("activeElementLabel") or "").strip()
            if ready_state or focus or self._snapshot.scroll_x or self._snapshot.scroll_y:
                lines.extend(["", "Page state:"])
                if ready_state:
                    lines.append(f"ready_state: {ready_state}")
                if focus:
                    lines.append(f"focus: {focus}")
                lines.append(f"scroll: {int(self._snapshot.scroll_x)}x{int(self._snapshot.scroll_y)}")
            if self._snapshot.target_kind or self._snapshot.target_label:
                lines.append("target: " + " ".join(filter(None, [
                    self._snapshot.target_kind,
                    self._snapshot.target_label,
                    self._snapshot.target_selector,
                ])))
            return {"ok": True, "text": "\n".join(lines).strip(), "state": self._snapshot.to_dict()}

    async def test_current_page(self) -> dict[str, Any]:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            assert self._page is not None
            await self._capture_state("test_current_page", capture_frame=True)
            try:
                data = await self._page.evaluate(
                    """() => {
                      const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
                      };
                      const label = (el) => String(
                        el.getAttribute('aria-label') || el.getAttribute('title') ||
                        el.getAttribute('placeholder') || el.innerText || el.value || el.textContent || ''
                      ).trim().replace(/\\s+/g, ' ').slice(0, 140);
                      const interactive = Array.from(document.querySelectorAll(
                        'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]'
                      )).filter(visible).slice(0, 40).map(el => ({
                        tag: el.tagName.toLowerCase(),
                        type: el.getAttribute('type') || '',
                        text: label(el),
                        disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
                      }));
                      const forms = Array.from(document.querySelectorAll('form')).filter(visible).length;
                      const headings = Array.from(document.querySelectorAll('h1,h2,h3')).filter(visible).slice(0, 12).map(label);
                      const text = (document.body && document.body.innerText || '').replace(/\\n{3,}/g, '\\n\\n').trim();
                      const viewportWidth = window.innerWidth || 0;
                      const viewportHeight = window.innerHeight || 0;
                      const documentWidth = Math.max(document.documentElement.scrollWidth || 0, document.body ? document.body.scrollWidth || 0 : 0);
                      const documentHeight = Math.max(document.documentElement.scrollHeight || 0, document.body ? document.body.scrollHeight || 0 : 0);
                      const viewportArea = Math.max(1, viewportWidth * viewportHeight);
                      const fixedOverlays = Array.from(document.querySelectorAll('body *')).filter(visible).map(el => {
                        const r = el.getBoundingClientRect();
                        const s = getComputedStyle(el);
                        return {
                          tag: el.tagName.toLowerCase(),
                          text: label(el),
                          position: s.position || '',
                          width: Math.round(r.width),
                          height: Math.round(r.height),
                          areaRatio: Math.round((r.width * r.height / viewportArea) * 1000) / 1000,
                        };
                      }).filter(item => (item.position === 'fixed' || item.position === 'sticky') && item.areaRatio >= 0.45).slice(0, 8);
                      const offscreenInteractive = Array.from(document.querySelectorAll(
                        'a,button,input,textarea,select,[role="button"],[role="link"],[contenteditable="true"]'
                      )).filter(visible).map(el => {
                        const r = el.getBoundingClientRect();
                        return {tag: el.tagName.toLowerCase(), text: label(el), left: Math.round(r.left), top: Math.round(r.top), right: Math.round(r.right), bottom: Math.round(r.bottom)};
                      }).filter(item => item.right < -8 || item.left > viewportWidth + 8 || item.bottom < -8 || item.top > viewportHeight + 8).slice(0, 12);
                      const unlabeledInteractive = interactive.filter(item => !item.text && !item.disabled).slice(0, 12);
                      const imagesMissingAlt = Array.from(document.querySelectorAll('img')).filter(visible).filter(img => !String(img.getAttribute('alt') || '').trim()).slice(0, 12).map(img => {
                        const r = img.getBoundingClientRect();
                        return {src: String(img.currentSrc || img.src || '').slice(0, 180), width: Math.round(r.width), height: Math.round(r.height)};
                      });
                      const h1Count = Array.from(document.querySelectorAll('h1')).filter(visible).length;
                      return {
                        url: location.href,
                        title: document.title || '',
                        readyState: document.readyState || '',
                        textSample: text.slice(0, 1800),
                        textLength: text.length,
                        interactive,
                        forms,
                        headings,
                        viewport: {width: viewportWidth, height: viewportHeight},
                        documentSize: {
                          width: documentWidth,
                          height: documentHeight,
                        },
                        layout: {
                          horizontalOverflowPx: Math.max(0, documentWidth - viewportWidth),
                          verticalOverflowPx: Math.max(0, documentHeight - viewportHeight),
                          fixedOverlays,
                          offscreenInteractive,
                        },
                        accessibility: {
                          unlabeledInteractive,
                          imagesMissingAlt,
                          h1Count,
                        },
                      };
                    }"""
                )
            except Exception as exc:
                return {"ok": False, "error": str(exc), "state": self._snapshot.to_dict()}

            console_recent = list(self._console_events[-20:])
            network_recent = list(self._network_events[-20:])
            console_findings = [
                ev for ev in console_recent
                if str(ev.get("type") or "").lower() in {"error", "warning"} or str(ev.get("kind") or "") == "pageerror"
            ]
            network_findings = [
                ev for ev in network_recent
                if int(ev.get("status") or 0) >= 400 or ev.get("kind") == "requestfailed"
            ]
            screenshot_analysis = _analyze_png_frame(self._frame_bytes)
            visual_findings = [
                str(item)
                for item in screenshot_analysis.get("findings", [])
                if str(item or "").strip()
            ]
            layout = data.get("layout") if isinstance(data.get("layout"), dict) else {}
            layout_findings: list[str] = []
            horizontal_overflow = int(layout.get("horizontalOverflowPx") or 0)
            if horizontal_overflow > 24:
                layout_findings.append(f"Page has horizontal overflow of {horizontal_overflow}px beyond the viewport.")
            fixed_overlays = layout.get("fixedOverlays") if isinstance(layout.get("fixedOverlays"), list) else []
            if fixed_overlays:
                top_overlay = fixed_overlays[0] if isinstance(fixed_overlays[0], dict) else {}
                overlay_label = str(top_overlay.get("text") or top_overlay.get("tag") or "fixed element")[:120]
                overlay_ratio = top_overlay.get("areaRatio", "unknown")
                layout_findings.append(f"Large fixed/sticky overlay detected ({overlay_label}, area ratio {overlay_ratio}).")
            offscreen_interactive = layout.get("offscreenInteractive") if isinstance(layout.get("offscreenInteractive"), list) else []
            if offscreen_interactive:
                layout_findings.append(f"{len(offscreen_interactive)} visible interactive control(s) are outside the viewport bounds.")
            accessibility = data.get("accessibility") if isinstance(data.get("accessibility"), dict) else {}
            accessibility_findings: list[str] = []
            unlabeled_interactive = accessibility.get("unlabeledInteractive") if isinstance(accessibility.get("unlabeledInteractive"), list) else []
            if unlabeled_interactive:
                accessibility_findings.append(f"{len(unlabeled_interactive)} visible enabled interactive control(s) have no accessible label.")
            images_missing_alt = accessibility.get("imagesMissingAlt") if isinstance(accessibility.get("imagesMissingAlt"), list) else []
            if images_missing_alt:
                accessibility_findings.append(f"{len(images_missing_alt)} visible image(s) are missing alt text.")
            h1_count = int(accessibility.get("h1Count") or 0)
            if h1_count <= 0 and str(data.get("textSample") or "").strip():
                accessibility_findings.append("No visible H1 heading was detected.")
            findings: list[str] = []
            if self._snapshot.status == "error" or self._snapshot.error:
                findings.append(f"Browser state reports {self._snapshot.status}: {self._snapshot.error}")
            if not self._frame_bytes or self._snapshot.frame_rev <= 0:
                findings.append("No current browser screenshot frame was captured.")
            if visual_findings:
                findings.extend(visual_findings)
            if layout_findings:
                findings.extend(layout_findings)
            if accessibility_findings:
                findings.extend(accessibility_findings)
            if console_findings:
                findings.append(f"{len(console_findings)} console/page error or warning event(s) captured.")
            if network_findings:
                findings.append(f"{len(network_findings)} failed or non-2xx/3xx network event(s) captured.")
            if not str(data.get("textSample") or "").strip():
                findings.append("Visible page text is empty.")
            if not data.get("interactive"):
                findings.append("No visible interactive controls were detected.")

            status = "pass" if not findings else "needs_review"
            permission = browser_permission_status(self.session_id)
            approval_mode = _current_approval_mode()
            active_goal = _active_goal_context(self.session_id)
            active_goal_text = str(active_goal.get("goal") or "").strip() if active_goal.get("present") else ""
            report = {
                "status": status,
                "url": data.get("url") or self._snapshot.url,
                "title": data.get("title") or self._snapshot.title,
                "ready_state": data.get("readyState") or self._snapshot.ready_state,
                "permission": permission,
                "approval_mode": approval_mode,
                "active_goal": active_goal,
                "screenshot": {
                    "available": bool(self._frame_bytes and self._snapshot.frame_rev > 0),
                    "frame_rev": self._snapshot.frame_rev,
                    "viewport": {
                        "width": self._snapshot.viewport_width,
                        "height": self._snapshot.viewport_height,
                    },
                    "analysis": screenshot_analysis,
                },
                "page": {
                    "text_length": int(data.get("textLength") or 0),
                    "interactive_count": len(data.get("interactive") or []),
                    "forms": int(data.get("forms") or 0),
                    "headings": data.get("headings") or [],
                    "viewport": data.get("viewport") or {},
                    "document_size": data.get("documentSize") or {},
                    "layout": layout,
                    "accessibility": accessibility,
                    "text_sample": data.get("textSample") or "",
                    "interactive_sample": data.get("interactive") or [],
                },
                "console_events": console_recent,
                "network_events": network_recent,
                "visual_findings": visual_findings,
                "layout_findings": layout_findings,
                "accessibility_findings": accessibility_findings,
                "findings": findings,
            }

            lines = [
                "🧪 **Browser Test Report**",
                "Status: " + ("PASS" if status == "pass" else "NEEDS REVIEW"),
                "URL: " + str(report["url"] or "about:blank"),
                "Title: " + str(report["title"] or "(untitled)"),
                "Ready state: " + str(report["ready_state"] or "unknown"),
                "Browser permission: " + str(permission.get("mode") or "none") + (" (granted)" if permission.get("granted") else " (locked)"),
                "Approval mode: " + str(approval_mode or "manual"),
                "Active goal: " + (active_goal_text or "none"),
                "Screenshot: " + ("available" if report["screenshot"]["available"] else "missing") + f" (rev {report['screenshot']['frame_rev']})",
                "",
                "Findings:",
            ]
            if findings:
                lines.extend(["- " + item for item in findings])
            else:
                lines.append("- No blocking browser, console, network, screenshot, or basic page-structure issues detected.")
            lines.extend([
                "",
                "Evidence:",
                f"- Text length: {report['page']['text_length']}",
                f"- Interactive controls: {report['page']['interactive_count']}",
                f"- Forms: {report['page']['forms']}",
                f"- Browser permission mode: {permission.get('mode') or 'none'}",
                f"- Approval mode: {approval_mode or 'manual'}",
                f"- Active goal: {active_goal_text or 'none'}",
                f"- Visual issues tracked: {len(visual_findings)}",
                f"- Layout issues tracked: {len(layout_findings)}",
                f"- Accessibility issues tracked: {len(accessibility_findings)}",
                f"- Unlabeled controls: {len(unlabeled_interactive)}",
                f"- Images missing alt: {len(images_missing_alt)}",
                f"- Visible H1 count: {h1_count}",
                f"- Horizontal overflow: {horizontal_overflow}px",
                f"- Large fixed overlays: {len(fixed_overlays)}",
                f"- Offscreen interactive controls: {len(offscreen_interactive)}",
                f"- Screenshot luma: {screenshot_analysis.get('avg_luma', 'unknown')} bright={screenshot_analysis.get('bright_ratio', 'unknown')} dark={screenshot_analysis.get('dark_ratio', 'unknown')}",
                f"- Console events tracked: {len(console_recent)}",
                f"- Network issues tracked: {len(network_findings)}",
            ])
            if report["page"]["headings"]:
                lines.append("- Headings: " + " | ".join(str(x) for x in report["page"]["headings"][:6]))
            if console_findings:
                lines.append("")
                lines.append("Console findings:")
                for ev in console_findings[:6]:
                    lines.append("- " + str(ev.get("type") or ev.get("kind") or "console") + ": " + str(ev.get("text") or "")[:220])
            if network_findings:
                lines.append("")
                lines.append("Network findings:")
                for ev in network_findings[:6]:
                    status_text = str(ev.get("status") or ev.get("error") or "failed")
                    lines.append("- " + status_text + " " + str(ev.get("method") or "") + " " + str(ev.get("url") or "")[:220])
            if report["page"]["interactive_sample"]:
                lines.append("")
                lines.append("Interactive sample:")
                for item in report["page"]["interactive_sample"][:8]:
                    label = item.get("text") or item.get("type") or item.get("tag") or "control"
                    disabled = " disabled" if item.get("disabled") else ""
                    lines.append("- " + str(item.get("tag") or "element") + disabled + ": " + str(label)[:160])
            if report["page"]["text_sample"]:
                lines.extend(["", "Visible text sample:", str(report["page"]["text_sample"])[:1200]])
            lines.extend([
                "",
                "Suggested next steps:",
                "- If findings exist, reproduce the listed issue and patch the page.",
                "- Re-run Test current page after changes to verify the fix.",
                "",
                "Fix findings prompt:",
                "Use this Browser Test Report as evidence. Identify affected files, patch the root cause, then re-run Test current page for the same URL and report the retest evidence.",
            ])
            return {"ok": True, "report": report, "text": "\n".join(lines).strip(), "state": self._snapshot.to_dict()}

    async def agent_action(self, action: str, payload: dict[str, Any] | None = None, *, origin_host: str | None = None) -> dict[str, Any]:
        payload = payload or {}
        act = str(action or "").strip().lower()
        if act in {"snapshot", "state"}:
            return await self.agent_snapshot(full=bool(payload.get("full")))
        if act in {"test_current_page", "test-page", "page_test"}:
            return await self.test_current_page()
        if act == "navigate":
            state = await self.navigate(str(payload.get("url") or ""), origin_host=origin_host)
            if state.status in {"blocked", "error"}:
                return {"ok": False, "error": state.error or state.status, "state": state.to_dict()}
            return {"ok": True, "state": state.to_dict()}
        if act == "open":
            return await self.agent_action("navigate", payload=payload, origin_host=origin_host)
        stale_frame = self._frame_rev_guard(payload)
        if stale_frame:
            return stale_frame
        if act == "click":
            ref = str(payload.get("ref") or "").strip()
            selector = str(payload.get("selector") or "").strip()
            target = self._resolve_ref_target(ref) if ref else None
            if selector:
                state = await self._click_selector(selector, str(payload.get("button") or "left"))
            elif target:
                state = await self.click(float(target.get("x", 0)), float(target.get("y", 0)), str(payload.get("button") or "left"))
            else:
                return {"ok": False, "error": f"Unknown browser ref: {ref}", "state": self._snapshot.to_dict()}
            return {"ok": True, "state": state.to_dict()}
        if act == "type":
            state = await self.type_text(str(payload.get("ref") or ""), str(payload.get("text") or ""), selector=str(payload.get("selector") or ""))
            return {"ok": True, "state": state.to_dict()}
        if act == "scroll":
            state = await self.scroll(str(payload.get("direction") or "down"))
            return {"ok": True, "state": state.to_dict()}
        if act == "press":
            state = await self.press(str(payload.get("key") or "Enter"))
            return {"ok": True, "state": state.to_dict()}
        if act == "wait":
            state = await self.wait(
                milliseconds=int(payload.get("ms", payload.get("milliseconds", 1000)) or 1000),
                selector=str(payload.get("selector") or ""),
                state=str(payload.get("state") or "visible"),
                until=str(payload.get("until") or ""),
            )
            return {"ok": True, "state": state.to_dict()}
        if act in {"back", "forward", "reload", "stop"}:
            state = await getattr(self, act)()
            return {"ok": True, "state": state.to_dict()}
        if act in {"sequence", "action_v1", "browser_action_v1"}:
            return await self.action_v1(payload, origin_host=origin_host)
        return {"ok": False, "error": f"Unsupported browser action: {action}", "state": self._snapshot.to_dict()}

    async def action_v1(self, payload: dict[str, Any] | None = None, *, origin_host: str | None = None) -> dict[str, Any]:
        payload = payload or {}
        stale_frame = self._frame_rev_guard(payload)
        if stale_frame:
            return stale_frame
        raw_steps = payload.get("steps") or payload.get("actions") or []
        if isinstance(raw_steps, dict):
            raw_steps = [raw_steps]
        steps = [step for step in raw_steps if isinstance(step, dict)]
        if not steps and str(payload.get("action") or "").strip():
            steps = [dict(payload)]
        results: list[dict[str, Any]] = []
        total = len(steps)
        for index, raw_step in enumerate(steps, start=1):
            action = str(raw_step.get("action") or raw_step.get("name") or raw_step.get("type") or "").strip().lower()
            if not action:
                continue
            step_payload = {
                key: value
                for key, value in raw_step.items()
                if key not in {"action", "name", "type"}
            }
            detail = self._action_payload(action, step_payload, step_index=index, step_total=total or None)
            self._update_snapshot(status="running", busy=True, last_action=action, last_action_detail=detail, error="")
            self._notify()
            result = await self.agent_action(action, payload=step_payload, origin_host=origin_host)
            result_entry = {
                "step": index,
                "action": action,
                "detail": detail,
                "ok": bool(result.get("ok", True)),
                "state": result.get("state") or self._snapshot.to_dict(),
            }
            if "text" in result:
                result_entry["text"] = result.get("text")
            if "report" in result:
                result_entry["report"] = result.get("report")
            results.append(result_entry)
            if not result.get("ok", True):
                return {
                    "ok": False,
                    "error": result.get("error") or f"Browser action failed at step {index}",
                    "steps": results,
                    "state": result.get("state") or self._snapshot.to_dict(),
                }
        response = {"ok": True, "steps": results, "state": self._snapshot.to_dict()}
        if len(results) == 1:
            if "text" in results[0]:
                response["text"] = results[0].get("text")
            if "report" in results[0]:
                response["report"] = results[0].get("report")
        return response


class BrowserManager:
    def __init__(self):
        self._sessions: dict[str, BrowserSession] = {}
        self._sessions_lock = threading.Lock()
        self._browser = None
        self._playwright = None
        self._browser_lock = asyncio.Lock()
        self._bootstrap_error = ""
        self._reaper_started = False

    def _ensure_thread(self) -> None:
        global _LOOP_THREAD, _LOOP
        if _LOOP_THREAD and _LOOP and _LOOP.is_running():
            return

        if _LOOP_THREAD and _LOOP_THREAD.is_alive():
            _READY.wait(timeout=20)
            return

        ready = threading.Event()

        def _runner() -> None:
            global _LOOP
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            _LOOP = loop
            ready.set()
            try:
                loop.run_forever()
            finally:
                try:
                    pending = asyncio.all_tasks(loop)
                    for task in pending:
                        task.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                except Exception:
                    pass
                try:
                    loop.close()
                except Exception:
                    pass

        _LOOP_THREAD = threading.Thread(target=_runner, name="browser-runtime-loop", daemon=True)
        _LOOP_THREAD.start()
        ready.wait(timeout=10)
        _READY.set()
        self._submit(self._bootstrap())

    async def _bootstrap(self) -> None:
        if self._browser is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover - Playwright is installed in the dev env
            self._bootstrap_error = f"Playwright unavailable: {exc}"
            logger.exception("Browser runtime bootstrap failed")
            return
        try:
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-dev-shm-usage",
                    "--disable-background-timer-throttling",
                    "--disable-backgrounding-occluded-windows",
                    "--disable-renderer-backgrounding",
                ],
            )
            self._start_reaper()
        except Exception as exc:
            self._bootstrap_error = str(exc)
            logger.exception("Browser runtime failed to launch Chromium")

    def _start_reaper(self) -> None:
        if self._reaper_started:
            return
        self._reaper_started = True

        async def _reap_loop() -> None:
            while True:
                await asyncio.sleep(60)
                await self._reap_idle_sessions()

        if _LOOP and _LOOP.is_running():
            asyncio.run_coroutine_threadsafe(_reap_loop(), _LOOP)

    async def _reap_idle_sessions(self) -> None:
        cutoff = time.time() - 1800
        to_close: list[str] = []
        with self._sessions_lock:
            for sid, session in self._sessions.items():
                if session._last_access_at < cutoff:
                    to_close.append(sid)
        for sid in to_close:
            try:
                session = self._sessions.get(sid)
                if session:
                    await session.close()
            finally:
                with self._sessions_lock:
                    self._sessions.pop(sid, None)
                _forget_session_permission(sid)

    def _submit(self, coro):
        self._ensure_thread()
        if not _LOOP:
            raise RuntimeError("Browser runtime loop failed to start")
        return asyncio.run_coroutine_threadsafe(coro, _LOOP).result()

    async def browser(self):
        async with self._browser_lock:
            if self._browser is None:
                await self._bootstrap()
            if self._browser is None:
                raise RuntimeError(self._bootstrap_error or "Browser runtime unavailable")
            return self._browser

    def _get_or_create_session(self, session_id: str) -> BrowserSession:
        sid = str(session_id or "").strip()
        if not sid:
            raise ValueError("session_id is required")
        with self._sessions_lock:
            session = self._sessions.get(sid)
            if session is None:
                session = BrowserSession(self, sid)
                self._sessions[sid] = session
            return session

    def ensure(self, session_id: str) -> dict[str, Any]:
        session = self._get_or_create_session(session_id)
        return self._submit(session.ensure()).to_dict()

    def snapshot(self, session_id: str) -> dict[str, Any]:
        session = self._get_or_create_session(session_id)
        return self._submit(session.snapshot()).to_dict()

    def frame_bytes(self, session_id: str) -> tuple[bytes, str]:
        session = self._get_or_create_session(session_id)
        self._submit(session.snapshot())
        return session._frame_bytes, session._frame_mime

    def diagnostics(self, session_id: str) -> dict[str, Any]:
        session = self._get_or_create_session(session_id)
        return self._submit(session.diagnostics())

    def subscribe(self, session_id: str) -> tuple[queue.Queue, dict[str, Any]]:
        session = self._get_or_create_session(session_id)
        snapshot = self._submit(session.ensure())
        q, _ = session.subscribe()
        return q, snapshot.to_dict()

    def unsubscribe(self, session_id: str, q: queue.Queue) -> None:
        session = self._get_or_create_session(session_id)
        session.unsubscribe(q)

    def reset(self, session_id: str) -> dict[str, Any]:
        session = self._get_or_create_session(session_id)
        return self._submit(session.reset()).to_dict()

    def control(self, session_id: str, action: str, *, origin_host: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        session = self._get_or_create_session(session_id)
        payload = payload or {}
        act = str(action or "").strip().lower()
        if act in {"navigate", "open"}:
            url = str(payload.get("url") or "").strip()
            if not url:
                raise ValueError("url is required")
            return self._submit(session.navigate(url, origin_host=origin_host)).to_dict()
        if act in {"click", "scroll", "move"}:
            stale_frame = session._frame_rev_guard(payload)
            if stale_frame:
                return stale_frame
        if act == "back":
            return self._submit(session.back()).to_dict()
        if act == "forward":
            return self._submit(session.forward()).to_dict()
        if act == "reload":
            return self._submit(session.reload()).to_dict()
        if act == "stop":
            return self._submit(session.stop()).to_dict()
        if act == "move":
            return self._submit(session.move(float(payload.get("x", 0)), float(payload.get("y", 0)))).to_dict()
        if act == "click":
            return self._submit(session.click(float(payload.get("x", 0)), float(payload.get("y", 0)), str(payload.get("button") or "left"))).to_dict()
        if act == "scroll":
            direction = str(payload.get("direction") or "").strip().lower()
            if not direction:
                try:
                    direction = "up" if float(payload.get("dy", 0) or 0) < 0 else "down"
                except Exception:
                    direction = "down"
            return self._submit(session.scroll(direction)).to_dict()
        if act == "wait":
            return self._submit(
                session.wait(
                    milliseconds=int(payload.get("ms", payload.get("milliseconds", 1000)) or 1000),
                    selector=str(payload.get("selector") or ""),
                    state=str(payload.get("state") or "visible"),
                    until=str(payload.get("until") or ""),
                )
            ).to_dict()
        if act in {"sequence", "action_v1", "browser_action_v1"}:
            return self._submit(session.action_v1(payload, origin_host=origin_host))
        if act == "reset":
            return self.reset(session_id)
        raise ValueError(f"Unsupported browser action: {action}")

    def agent_snapshot(self, session_id: str, full: bool = False) -> dict[str, Any]:
        session = self._get_or_create_session(session_id)
        return self._submit(session.agent_snapshot(full=full))

    def agent_action(self, session_id: str, action: str, payload: dict[str, Any] | None = None, *, origin_host: str | None = None) -> dict[str, Any]:
        session = self._get_or_create_session(session_id)
        return self._submit(session.agent_action(action, payload=payload, origin_host=origin_host))

    def action_v1(self, session_id: str, payload: dict[str, Any] | None = None, *, origin_host: str | None = None) -> dict[str, Any]:
        session = self._get_or_create_session(session_id)
        return self._submit(session.action_v1(payload, origin_host=origin_host))

    def close(self) -> None:
        sessions = list(self._sessions.values())
        for session in sessions:
            try:
                self._submit(session.close())
            except Exception:
                pass
        self._sessions.clear()
        if _LOOP and _LOOP.is_running():
            try:
                if self._browser is not None:
                    self._submit(self._browser.close())
            except Exception:
                pass
            try:
                if self._playwright is not None:
                    self._submit(self._playwright.stop())
            except Exception:
                pass


def get_browser_manager() -> BrowserManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = BrowserManager()
    return _MANAGER


def browser_state(session_id: str) -> dict[str, Any]:
    return get_browser_manager().snapshot(session_id)


def browser_agent_context(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    state = browser_state(sid) if sid else {"session_id": "", "status": "idle", "url": "", "error": ""}
    permission = browser_permission_status(sid)
    approval_mode = _current_approval_mode()
    active_goal = _active_goal_context(sid)
    can_watch = bool(permission.get("can_watch"))
    can_control = bool(permission.get("can_control"))
    frame_url = str(state.get("frame_url") or "")
    frame_rev = state.get("frame_rev")
    now = time.time()
    updated_at = state.get("updated_at")
    try:
        frame_age_ms = round(max(0.0, now - float(updated_at)) * 1000.0)
    except (TypeError, ValueError):
        frame_age_ms = None
    busy = bool(state.get("busy"))
    page_needs_navigation = bool(not state.get("url") or state.get("url") == "about:blank")
    frame_ready = bool(frame_url and frame_rev is not None and not busy)
    browser_ready = bool(frame_ready and not busy and not page_needs_navigation and not state.get("error"))
    blocked_reasons: list[str] = []
    if not can_watch:
        blocked_reasons.append("browser_permission_required")
    if page_needs_navigation:
        blocked_reasons.append("browser_has_no_page")
    if busy:
        blocked_reasons.append("browser_busy")
    if not frame_ready:
        blocked_reasons.append("rendered_frame_not_ready")
    if state.get("error"):
        blocked_reasons.append("browser_error")
    if state.get("allowed") is False:
        blocked_reasons.append("target_not_allowed")
    visual_analysis: dict[str, Any] = {
        "available": False,
        "permission_required": not can_watch,
        "findings": ["Browser read permission is required for visual frame analysis."] if not can_watch else [],
    }
    visual_findings: list[str] = []
    if can_watch and frame_ready:
        try:
            frame_bytes, frame_mime = get_browser_manager().frame_bytes(sid)
            if str(frame_mime or "").lower().startswith("image/png"):
                visual_analysis = _analyze_png_frame(frame_bytes)
            else:
                visual_analysis = {
                    "available": bool(frame_bytes),
                    "mime": frame_mime,
                    "findings": ["Current browser frame is not a PNG image."],
                }
        except Exception as exc:
            visual_analysis = {
                "available": False,
                "error": str(exc),
                "findings": ["Browser visual frame analysis failed."],
            }
    visual_findings = [
        str(item)
        for item in (visual_analysis.get("findings") if isinstance(visual_analysis, dict) else []) or []
        if str(item or "").strip()
    ]
    if visual_findings and can_watch and not page_needs_navigation:
        blocked_reasons.append("visual_frame_findings")
    technical_diagnostics: dict[str, Any] = {
        "available": False,
        "permission_required": not can_watch,
        "console_error_count": 0,
        "network_error_count": 0,
        "findings": ["Browser read permission is required for console and network diagnostics."] if not can_watch else [],
    }
    technical_findings: list[str] = []
    if can_watch:
        try:
            diagnostics = get_browser_manager().diagnostics(sid)
            console_error_count = int(diagnostics.get("console_error_count") or 0)
            network_error_count = int(diagnostics.get("network_error_count") or 0)
            technical_findings = []
            if console_error_count:
                technical_findings.append(f"{console_error_count} console/page error or warning event(s) captured.")
            if network_error_count:
                technical_findings.append(f"{network_error_count} failed or non-2xx/3xx network event(s) captured.")
            page_error = str(diagnostics.get("page_error") or "").strip()
            if page_error:
                technical_findings.append("Page error captured: " + page_error[:240])
            technical_diagnostics = {
                "available": True,
                "console_error_count": console_error_count,
                "network_error_count": network_error_count,
                "page_error": page_error,
                "console_findings": diagnostics.get("console_findings") or [],
                "network_findings": diagnostics.get("network_findings") or [],
                "findings": technical_findings,
            }
        except Exception as exc:
            technical_diagnostics = {
                "available": False,
                "error": str(exc),
                "console_error_count": 0,
                "network_error_count": 0,
                "findings": ["Browser technical diagnostics failed."],
            }
    if technical_findings and can_watch:
        blocked_reasons.append("technical_page_findings")
    next_actions: list[str] = []
    if not can_watch:
        next_actions.append("request_browser_permission")
    if page_needs_navigation:
        next_actions.append("navigate_to_url")
    if busy:
        next_actions.append("wait_for_browser_idle")
    if not frame_ready:
        next_actions.append("wait_for_rendered_frame")
    if can_watch and frame_ready and not busy:
        next_actions.append("run_browser_qa")
    def _action_descriptor(
        action_id: str,
        *,
        label: str,
        method: str,
        endpoint: str,
        required_permission: str,
        available: bool,
        blocked: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        permission_steps: list[str] | None = None,
    ) -> dict[str, Any]:
        clean_blocked = [str(item) for item in (blocked or []) if str(item or "").strip()]
        return {
            "id": action_id,
            "label": label,
            "method": method,
            "endpoint": endpoint,
            "required_permission": required_permission,
            "available": bool(available) and not clean_blocked,
            "blocked_reasons": clean_blocked,
            "approval_mode": approval_mode,
            "active_goal": active_goal,
            "permission_steps": [str(item) for item in (permission_steps or []) if str(item or "").strip()],
            "permission_step_labels": [
                _permission_step_label(str(item))
                for item in (permission_steps or [])
                if str(item or "").strip()
            ],
            "payload": payload or {},
            "query": query or {},
        }

    def _read_blockers(*extra: str) -> list[str]:
        reasons: list[str] = []
        if not sid:
            reasons.append("session_id_required")
        if not can_watch:
            reasons.append("browser_read_permission_required")
        reasons.extend(extra)
        return reasons

    def _control_blockers(*extra: str, include_busy: bool = True) -> list[str]:
        reasons: list[str] = []
        if not sid:
            reasons.append("session_id_required")
        if not can_control:
            reasons.append("browser_control_permission_required")
        if include_busy and busy:
            reasons.append("browser_busy")
        reasons.extend(extra)
        return reasons

    wait_blockers = ["session_id_required"] if not sid else []
    qa_extra_blockers: list[str] = []
    snapshot_extra_blockers: list[str] = []
    control_extra_blockers: list[str] = []
    if busy:
        qa_extra_blockers.append("browser_busy")
        snapshot_extra_blockers.append("browser_busy")
    if not frame_ready:
        qa_extra_blockers.append("rendered_frame_not_ready")
        snapshot_extra_blockers.append("rendered_frame_not_ready")
        control_extra_blockers.append("rendered_frame_not_ready")
    if page_needs_navigation:
        qa_extra_blockers.append("browser_has_no_page")
        snapshot_extra_blockers.append("browser_has_no_page")
    if state.get("allowed") is False:
        qa_extra_blockers.append("target_not_allowed")
        control_extra_blockers.append("target_not_allowed")

    read_permission_steps = ["enable_browser_watch"] if not can_watch else []
    control_permission_steps: list[str] = []
    if not can_watch:
        control_permission_steps.append("enable_browser_watch")
    if not can_control:
        control_permission_steps.append("enable_browser_control")

    available_actions: dict[str, Any] = {
        "request_read_permission": _action_descriptor(
            "request_read_permission",
            label="Request browser read permission",
            method="POST",
            endpoint="/api/browser/permission",
            required_permission="none",
            available=bool(sid),
            blocked=wait_blockers,
            payload={"session_id": sid, "mode": "read"},
        ),
        "request_control_permission": _action_descriptor(
            "request_control_permission",
            label="Request browser control permission",
            method="POST",
            endpoint="/api/browser/permission",
            required_permission="none",
            available=bool(sid),
            blocked=wait_blockers,
            payload={"session_id": sid, "mode": "control"},
        ),
        "wait_for_idle": _action_descriptor(
            "wait_for_idle",
            label="Wait for browser idle/rendered frame",
            method="POST",
            endpoint="/api/browser/agent-control",
            required_permission="control",
            available=bool(sid and can_control),
            blocked=_control_blockers(include_busy=False),
            permission_steps=control_permission_steps,
            payload={"session_id": sid, "action": "wait", "ms": 500},
        ),
        "navigate": _action_descriptor(
            "navigate",
            label="Navigate browser to URL",
            method="POST",
            endpoint="/api/browser/agent-control",
            required_permission="control",
            available=bool(sid and can_control and not busy),
            blocked=_control_blockers(),
            permission_steps=control_permission_steps,
            payload={"session_id": sid, "action": "navigate", "url": "<url>"},
        ),
        "snapshot": _action_descriptor(
            "snapshot",
            label="Read browser snapshot",
            method="POST",
            endpoint="/api/browser/agent-control",
            required_permission="read",
            available=bool(sid and can_watch and frame_ready and not busy and not snapshot_extra_blockers),
            blocked=_read_blockers(*snapshot_extra_blockers),
            permission_steps=read_permission_steps,
            payload={"session_id": sid, "action": "snapshot"},
        ),
        "qa": _action_descriptor(
            "qa",
            label="Run visual and technical browser QA",
            method="GET",
            endpoint="/api/browser/qa",
            required_permission="read",
            available=bool(sid and can_watch and frame_ready and not busy and not qa_extra_blockers),
            blocked=_read_blockers(*qa_extra_blockers),
            permission_steps=read_permission_steps,
            query={"session_id": sid},
        ),
        "control_sequence": _action_descriptor(
            "control_sequence",
            label="Send browser control/action sequence",
            method="POST",
            endpoint="/api/browser/agent-control",
            required_permission="control",
            available=bool(sid and can_control and frame_ready and not busy and state.get("allowed") is not False),
            blocked=_control_blockers(*control_extra_blockers),
            permission_steps=control_permission_steps,
            payload={"session_id": sid, "action": "action_v1", "expected_frame_rev": frame_rev, "steps": []},
        ),
    }

    recommended_action = "qa"
    control_recommended_action = "control_sequence"
    if not sid:
        recommended_action = "none"
        control_recommended_action = "none"
    elif not can_watch:
        recommended_action = "request_read_permission"
        control_recommended_action = "request_control_permission"
    elif page_needs_navigation:
        recommended_action = "navigate" if can_control else "request_control_permission"
        control_recommended_action = "navigate" if can_control else "request_control_permission"
    elif busy:
        recommended_action = "wait_for_idle" if can_control else "request_control_permission"
        control_recommended_action = "wait_for_idle" if can_control else "request_control_permission"
    elif not frame_ready:
        recommended_action = "wait_for_idle" if can_control else "request_control_permission"
        control_recommended_action = "wait_for_idle" if can_control else "request_control_permission"
    elif state.get("allowed") is False:
        recommended_action = "navigate" if can_control else "request_control_permission"
        control_recommended_action = "navigate" if can_control else "request_control_permission"
    elif visual_findings or technical_findings:
        recommended_action = "qa"
    elif not can_control:
        control_recommended_action = "request_control_permission"
    return {
        "session_id": sid,
        "browser": state,
        "permission": permission,
        "approval_mode": approval_mode,
        "approval_modes": ["manual", "smart", "off"],
        "active_goal": active_goal,
        "rendered_frame_ready": frame_ready,
        "frame_age_ms": frame_age_ms,
        "expected_frame_rev": frame_rev,
        "page_needs_navigation": page_needs_navigation,
        "browser_ready": browser_ready,
        "visual_analysis": visual_analysis,
        "visual_findings": visual_findings,
        "technical_diagnostics": technical_diagnostics,
        "technical_findings": technical_findings,
        "blocked_reasons": blocked_reasons,
        "agent_can_operate": can_control and frame_ready and not busy,
        "agent_can_assess": can_watch and frame_ready and not busy,
        "next_actions": next_actions,
        "available_actions": available_actions,
        "recommended_action": recommended_action,
        "control_recommended_action": control_recommended_action,
        "generated_at": now,
    }


def browser_frame_bytes(session_id: str) -> tuple[bytes, str]:
    return get_browser_manager().frame_bytes(session_id)


def browser_control(session_id: str, action: str, *, origin_host: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    token = (payload or {}).get("permission_token") or (payload or {}).get("browser_permission_token")
    if token and not browser_permission_token_valid(session_id, str(token), _permission_required_for_action(action)):
        required = _permission_required_for_action(action)
        result = _permission_required_payload(session_id, required)
        result["status"] = 403
        return result
    return get_browser_manager().control(session_id, action, origin_host=origin_host, payload=payload)


def browser_action_v1(session_id: str, payload: dict[str, Any] | None = None, *, origin_host: str | None = None) -> dict[str, Any]:
    payload = payload or {}
    action = str(payload.get("action") or payload.get("name") or payload.get("type") or "").strip().lower()
    required = _permission_required_for_action(action)
    token = payload.get("permission_token") or payload.get("browser_permission_token")
    if token and not browser_permission_token_valid(session_id, str(token), required):
        result = _permission_required_payload(session_id, required)
        result["status"] = 403
        return result
    user_read_action = (
        bool(payload.get("_user_initiated"))
        and not payload.get("steps")
        and not payload.get("actions")
        and action in _READ_ACTIONS
    )
    if user_read_action:
        return get_browser_manager().action_v1(session_id, payload=payload, origin_host=origin_host)
    if not _has_permission(session_id, required):
        return _permission_required_payload(session_id, required)
    return get_browser_manager().action_v1(session_id, payload=payload, origin_host=origin_host)


def browser_subscribe(session_id: str) -> tuple[queue.Queue, dict[str, Any]]:
    return get_browser_manager().subscribe(session_id)


def browser_unsubscribe(session_id: str, q: queue.Queue) -> None:
    get_browser_manager().unsubscribe(session_id, q)


def browser_reset(session_id: str) -> dict[str, Any]:
    return get_browser_manager().reset(session_id)


def browser_agent_control(session_id: str, action: str, *, payload: dict[str, Any] | None = None, origin_host: str | None = None) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "code": "invalid_request", "error": "session_id is required"}
    act = str(action or "").strip().lower()
    required = _permission_required_for_action(act)
    if act not in _READ_ACTIONS and act not in _CONTROL_ACTIONS:
        return {"ok": False, "code": "unsupported_browser_action", "error": f"Unsupported browser action: {action}"}
    if not _has_permission(sid, required):
        return _permission_required_payload(sid, required)
    token = (payload or {}).get("permission_token") or (payload or {}).get("browser_permission_token")
    if token and not browser_permission_token_valid(sid, str(token), required):
        return _permission_required_payload(sid, required)
    if act in {"snapshot", "state"}:
        return get_browser_manager().agent_snapshot(sid, full=bool((payload or {}).get("full")))
    if act in _QA_ACTIONS:
        return get_browser_manager().agent_action(sid, "test_current_page", payload=payload, origin_host=origin_host)
    if act in {"sequence", "action_v1", "browser_action_v1"}:
        return get_browser_manager().action_v1(sid, payload=payload, origin_host=origin_host)
    return get_browser_manager().agent_action(sid, act, payload=payload, origin_host=origin_host)


def _shutdown_browser_manager() -> None:
    global _MANAGER
    if _MANAGER is None:
        return
    try:
        _MANAGER.close()
    finally:
        _MANAGER = None


atexit.register(_shutdown_browser_manager)
