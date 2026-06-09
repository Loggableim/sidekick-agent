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
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_LOOP_THREAD: threading.Thread | None = None
_LOOP: asyncio.AbstractEventLoop | None = None
_READY = threading.Event()
_MANAGER: "BrowserManager | None" = None
_PERMISSIONS: dict[str, dict[str, Any]] = {}
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
_READ_ACTIONS = {"snapshot", "state"}
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


def _normalize_permission_mode(mode: str | None) -> str:
    value = str(mode or "").strip().lower()
    return value if value in _BROWSER_PERMISSION_MODES else "none"


def browser_permission_status(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"session_id": "", "mode": "none", "granted": False}
    with _PERMISSIONS_LOCK:
        current = dict(_PERMISSIONS.get(sid) or {})
        if current and time.time() - float(current.get("updated_at") or 0) > _PERMISSION_TTL_SECONDS:
            _PERMISSIONS.pop(sid, None)
            current = {}
    mode = _normalize_permission_mode(current.get("mode"))
    return {
        "session_id": sid,
        "mode": mode,
        "granted": mode != "none",
        "updated_at": current.get("updated_at"),
        "expires_at": current.get("expires_at"),
    }


def browser_permission_grant(session_id: str, mode: str = "control") -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    normalized = _normalize_permission_mode(mode)
    if normalized == "none":
        normalized = "control"
    token = secrets.token_urlsafe(24)
    now = time.time()
    with _PERMISSIONS_LOCK:
        _PERMISSIONS[sid] = {
            "mode": normalized,
            "token": token,
            "updated_at": now,
            "expires_at": now + _PERMISSION_TTL_SECONDS,
        }
    return browser_permission_status(sid)


def browser_permission_revoke(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"session_id": "", "mode": "none", "granted": False}
    with _PERMISSIONS_LOCK:
        _PERMISSIONS.pop(sid, None)
    return browser_permission_status(sid)


def _permission_required_payload(session_id: str, required: str) -> dict[str, Any]:
    return {
        "ok": False,
        "code": "browser_permission_required",
        "error": "Browser permission is required for this action.",
        "required_mode": required,
        "permission": browser_permission_status(session_id),
    }


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


def browser_permission_token(session_id: str) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    browser_permission_status(sid)
    with _PERMISSIONS_LOCK:
        return str((_PERMISSIONS.get(sid) or {}).get("token") or "")


def browser_permission_token_valid(session_id: str, token: str | None, required: str) -> bool:
    sid = str(session_id or "").strip()
    supplied = str(token or "").strip()
    if not sid or not supplied or not _has_permission(sid, required):
        return False
    expected = browser_permission_token(sid)
    return bool(expected and secrets.compare_digest(expected, supplied))


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
    raw = _safe_getenv("SIDEKICK_BROWSER_ALLOW_HOSTS") or _safe_getenv("HERMES_BROWSER_ALLOW_HOSTS")
    if not raw:
        return []
    parts = [p.strip() for p in re.split(r"[,\s]+", raw) if p.strip()]
    return parts


def _env_require_allowlist() -> bool:
    raw = _safe_getenv("SIDEKICK_BROWSER_REQUIRE_ALLOWLIST") or _safe_getenv("HERMES_BROWSER_REQUIRE_ALLOWLIST")
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

    def _touch(self) -> None:
        self._last_access_at = time.time()

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
            asyncio.create_task(self._capture_state("pageerror", capture_frame=False))

        page.on("framenavigated", _schedule_nav)
        page.on("load", _schedule_load)
        page.on("pageerror", _page_error)

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

    async def snapshot(self) -> BrowserSnapshot:
        async with self._lock:
            self._touch()
            await self._ensure_created_locked()
            return self._snapshot

    def _update_snapshot(self, **kwargs: Any) -> BrowserSnapshot:
        for key, value in kwargs.items():
            setattr(self._snapshot, key, value)
        self._snapshot.updated_at = time.time()
        self._snapshot.frame_url = f"/api/browser/frame?session_id={self.session_id}&rev={self._snapshot.frame_rev}"
        return self._snapshot

    def _action_payload(self, action: str, payload: dict[str, Any] | None = None, *, step_index: int | None = None, step_total: int | None = None) -> str:
        return _action_detail_text(action, payload, step_index=step_index, step_total=step_total)

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
                    return String(
                      node.getAttribute('aria-label') ||
                      node.getAttribute('title') ||
                      node.getAttribute('placeholder') ||
                      node.innerText ||
                      node.value ||
                      node.textContent ||
                      ''
                    ).trim().replace(/\\s+/g, ' ');
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
                self._frame_bytes = await self._page.screenshot(
                    type="png",
                    animations="disabled",
                    caret="hide",
                    omit_background=False,
                    scale="device",
                )
                self._snapshot.frame_rev += 1
            except Exception as exc:
                self._frame_bytes = _blank_png_bytes()
                self._update_snapshot(status="error", error=str(exc), busy=False)
        self._notify()
        return self._snapshot

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
                await self._page.goto(target, wait_until="domcontentloaded")
            except Exception as exc:
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
                await self._page.go_back(wait_until="domcontentloaded")
            except Exception as exc:
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
                await self._page.go_forward(wait_until="domcontentloaded")
            except Exception as exc:
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
                await self._page.reload(wait_until="domcontentloaded")
            except Exception as exc:
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
                      const label = (el) => (
                        el.getAttribute('aria-label') || el.getAttribute('title') ||
                        el.getAttribute('placeholder') || el.innerText || el.value || el.textContent || ''
                      ).trim().replace(/\\s+/g, ' ');
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

    async def agent_action(self, action: str, payload: dict[str, Any] | None = None, *, origin_host: str | None = None) -> dict[str, Any]:
        payload = payload or {}
        act = str(action or "").strip().lower()
        if act in {"snapshot", "state"}:
            return await self.agent_snapshot(full=bool(payload.get("full")))
        if act == "navigate":
            state = await self.navigate(str(payload.get("url") or ""), origin_host=origin_host)
            if state.status in {"blocked", "error"}:
                return {"ok": False, "error": state.error or state.status, "state": state.to_dict()}
            return {"ok": True, "state": state.to_dict()}
        if act == "open":
            return await self.agent_action("navigate", payload=payload, origin_host=origin_host)
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
            results.append({
                "step": index,
                "action": action,
                "detail": detail,
                "ok": bool(result.get("ok", True)),
                "state": result.get("state") or self._snapshot.to_dict(),
            })
            if not result.get("ok", True):
                return {
                    "ok": False,
                    "error": result.get("error") or f"Browser action failed at step {index}",
                    "steps": results,
                    "state": result.get("state") or self._snapshot.to_dict(),
                }
        return {"ok": True, "steps": results, "state": self._snapshot.to_dict()}


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
                browser_permission_revoke(sid)

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
        self._submit(session.ensure())
        return session._frame_bytes, session._frame_mime

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
    token = (payload or {}).get("permission_token") or (payload or {}).get("browser_permission_token")
    if token and not browser_permission_token_valid(session_id, str(token), "control"):
        result = _permission_required_payload(session_id, "control")
        result["status"] = 403
        return result
    if not _has_permission(session_id, "control"):
        return _permission_required_payload(session_id, "control")
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
    if act in _READ_ACTIONS:
        return get_browser_manager().agent_snapshot(sid, full=bool((payload or {}).get("full")))
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
