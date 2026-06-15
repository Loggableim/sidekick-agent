"""
Sidekick -- Main server entry point.
Thin routing shell: imports Handler, delegates to api/routes.py, runs server.
All business logic lives in api/*.
"""
import logging
import os
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import redirect_stdout, redirect_stderr

# ── Test-mode network isolation ─────────────────────────────────────────────
# When `HERMES_WEBUI_TEST_NETWORK_BLOCK=1` is set in the environment, refuse
# outbound socket connections to anything that is not loopback / RFC1918 /
# link-local / reserved-TLD. This catches accidental real outbound (forgotten
# mocks, leaked credentials triggering SDK init, new code paths bypassing an
# existing mock) so the test suite stays hermetic and fast.
#
# tests/conftest.py sets this env var on every test_server subprocess so the
# server.py-side network isolation matches the pytest-process-side isolation
# already installed there.
#
# A test that legitimately needs real outbound spawns the server with the env
# var unset (no current callers — every test_server-using test should be
# mockable).
if (os.environ.get("SIDEKICK_WEBUI_TEST_NETWORK_BLOCK") or os.environ.get("HERMES_WEBUI_TEST_NETWORK_BLOCK", "")).strip() in ("1", "true", "yes"):
    _REAL_CREATE_CONN = socket.create_connection
    _REAL_SOCK_CONNECT = socket.socket.connect

    import re as _re

    def _re_match_unique_local_ipv6(h):
        """Match IPv6 fc00::/7 (canonical syntax). Tighter than startswith('fc')
        so we don't mistakenly classify hostnames like 'food.example.com' as local."""
        return bool(_re.match(r"^f[cd][0-9a-f]{0,2}:", h))

    def _addr_is_local(host):
        if not isinstance(host, str):
            return False
        h = host.strip().lower()
        if not h:
            return False
        # IPv6 unique-local fc00::/7: require hex pair + colon to avoid
        # matching hostnames like "food.example.com" or "fdsa.test".
        if h in ("::1", "0:0:0:0:0:0:0:1") or h.startswith("fe80:") or _re_match_unique_local_ipv6(h):
            return True
        if h == "localhost" or h.endswith(".localhost"):
            return True
        if h.endswith(".local") or h.endswith(".test") or h.endswith(".invalid"):
            return True
        if h == "example.com" or h.endswith(".example.com"):
            return True
        if h == "example.net" or h.endswith(".example.net"):
            return True
        if h == "example.org" or h.endswith(".example.org"):
            return True
        if h.endswith(".example"):
            return True
        if h and h[0].isdigit() and h.count(".") == 3:
            try:
                o1, o2, o3, o4 = [int(p) for p in h.split(".")]
            except ValueError:
                return False
            if o1 == 127:
                return True
            if o1 == 10:
                return True
            if o1 == 192 and o2 == 168:
                return True
            if o1 == 172 and 16 <= o2 <= 31:
                return True
            if o1 == 169 and o2 == 254:
                return True
            if o1 == 203 and o2 == 0 and o3 == 113:
                return True
        return False

    def _blocked_create_connection(address, *a, **kw):
        try:
            host = address[0]
        except (TypeError, IndexError):
            host = ""
        if _addr_is_local(host):
            return _REAL_CREATE_CONN(address, *a, **kw)
        raise OSError(
            f"sidekick test network isolation (server.py): outbound to {address!r} blocked"
        )

    def _blocked_socket_connect(self, address):
        try:
            host = address[0]
        except (TypeError, IndexError):
            host = ""
        if _addr_is_local(host):
            return _REAL_SOCK_CONNECT(self, address)
        raise OSError(
            f"sidekick test network isolation (server.py): socket.connect to {address!r} blocked"
        )

    socket.create_connection = _blocked_create_connection
    socket.socket.connect = _blocked_socket_connect


try:
    import resource
except ImportError:  # pragma: no cover - resource is Unix-only
    resource = None
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class _Tee:
    def __init__(self, *streams):
        self._streams = streams

    def write(self, data):
        for stream in self._streams:
            try:
                stream.write(data)
                stream.flush()
            except Exception:
                pass

    def flush(self):
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:
                pass


def _resolve_log_file():
    log_file = (os.environ.get("SIDEKICK_WEBUI_LOG_FILE") or os.environ.get("HERMES_WEBUI_LOG_FILE", "")).strip()
    if log_file:
        return log_file
    return str(STATE_DIR.parent / "webui.log")

from web.api.auth import check_auth
from web.api.config import HOST, PORT, STATE_DIR, SESSION_DIR, DEFAULT_WORKSPACE
from web.api.helpers import j, get_profile_cookie
from web.api.profiles import set_request_profile, clear_request_profile
from web.api.routes import handle_delete, handle_get, handle_patch, handle_post
from web.api.routes import _setup_workspace_from_request, _teardown_workspace_context
from web.api.startup import auto_install_agent_deps, fix_credential_permissions
from web.api.updates import WEBUI_VERSION


class QuietHTTPServer(ThreadingHTTPServer):
    """Custom HTTP server that silently handles common network errors."""
    daemon_threads = True
    request_queue_size = 64
    allow_reuse_address = True  # Allow bind over TIME_WAIT; port-ghosting is prevented by start-windows.bat cleanup

    def __init__(self, *args, **kwargs):
        server_address = args[0] if args else kwargs.get('server_address', None)
        if server_address:
            host = server_address[0]
            # Robust IPv6 detection: try getaddrinfo to resolve the host
            if self._is_ipv6_address(host):
                self.address_family = socket.AF_INET6
        super().__init__(*args, **kwargs)
        self.accept_loop_requests_total = 0
        self.accept_loop_last_request_at = 0.0
        self._shutdown_requested = threading.Event()

    @staticmethod
    def _is_ipv6_address(host):
        """Return True if *host* is a valid IPv6 literal (not an IPv4 hostname with colons)."""
        if not host or ':' not in host:
            return False
        try:
            socket.getaddrinfo(host, None, socket.AF_INET6)
            return True
        except (socket.gaierror, OSError):
            return False

    def _handle_request_noblock(self):
        """Record accept-loop progress before dispatching a request handler.

        A process can be alive and still stop accepting/dispatching requests.
        Exposing this heartbeat on /health gives supervisors and watchdogs a
        cheap signal that the accept loop is still moving.

        Note: this method is called only from the single ``serve_forever()``
        thread in CPython socketserver, so the un-locked ``+=`` increment is
        safe — there is no other thread mutating these counters. The /health
        readers may see a stale value momentarily but never an inconsistent
        one (Python int reads are atomic). Per Opus advisor on stage-297.
        """
        self.accept_loop_requests_total += 1
        self.accept_loop_last_request_at = time.time()
        return super()._handle_request_noblock()
    
    def handle_error(self, request, client_address):
        """Override to suppress logging for common client disconnect errors."""
        exc_type, exc_value, _ = sys.exc_info()

        # Silently ignore common connection errors caused by client disconnects
        if exc_type in (ConnectionResetError, BrokenPipeError, ConnectionAbortedError, TimeoutError):
            return

        # Also handle socket errors that indicate client disconnect
        if issubclass(exc_type, OSError):
            # errno 32 = EPIPE, 54/104 = ECONNRESET (BSD/Linux), 110 = ETIMEDOUT
            if getattr(exc_value, 'errno', None) in (32, 54, 104, 110):
                return
            # Windows winsock errors: 10053 = WSAECONNABORTED, 10054 = WSAECONNRESET
            if getattr(exc_value, 'winerror', None) in (10053, 10054, 10058):
                return

        # For other errors, use default logging
        super().handle_error(request, client_address)


def create_server(host: str | None = None, port: int | None = None) -> QuietHTTPServer:
    """Create a dashboard-compatible HTTP server for tests and callers.

    Older tests import ``create_server()`` directly. Keep that compatibility
    shim so they can bind the legacy web surface without duplicating the
    ``QuietHTTPServer`` construction logic.
    """
    resolved_host = HOST if host is None else host
    resolved_port = PORT if port is None else int(port)
    return QuietHTTPServer((resolved_host, resolved_port), Handler)


class Handler(BaseHTTPRequestHandler):
    timeout = 30  # seconds — kills idle/incomplete connections to prevent thread exhaustion
    
    def setup(self):
        """Set socket options for each accepted connection."""
        super().setup()
        # TCP_NODELAY — universal, disables Nagle for HTTP latency
        try:
            self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass
        # SO_KEEPALIVE — universal master switch (must be set before timing params)
        try:
            self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        except OSError:
            pass
        # Per-platform timing parameters
        if hasattr(socket, 'TCP_KEEPIDLE'):  # Linux
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 10)
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 5)
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
            except OSError:
                pass
        elif hasattr(socket, 'TCP_KEEPALIVE'):  # macOS
            try:
                self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPALIVE, 10)
            except OSError:
                pass
    _ver_suffix = WEBUI_VERSION.removeprefix('v')
    server_version = ('SidekickWebUI/' + _ver_suffix) if _ver_suffix != 'unknown' else 'SidekickWebUI'
    _CSP_REPORT_ONLY = (
        "default-src 'self' https://*.cloudflareaccess.com; "
        "base-uri 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://static.cloudflareinsights.com; "
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://fonts.googleapis.com; "
        "img-src 'self' data: https: blob:; "
        "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.gstatic.com; "
        "media-src 'self' data: blob:; "
        "connect-src 'self' https://cdn.jsdelivr.net http://127.0.0.1:* http://localhost:* http://192.168.1.110:* ws://127.0.0.1:* ws://localhost:* ws://192.168.1.110:*; "
        "manifest-src 'self' https://*.cloudflareaccess.com; "
        "form-action 'self'; "
        "report-uri /api/csp-report"
    )

    @classmethod
    def csp_report_only_policy(cls) -> str:
        return cls._CSP_REPORT_ONLY

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy-Report-Only", self.csp_report_only_policy())
        super().end_headers()

    def log_message(self, fmt, *args): pass  # suppress default Apache-style log

    def log_request(self, code: str='-', size: str='-') -> None:
        """Structured JSON logs for each request."""
        import json as _json
        duration_ms = round((time.time() - getattr(self, '_req_t0', time.time())) * 1000, 1)
        record = _json.dumps({
            'ts': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
            'method': self.command or '-',
            'path': self.path or '-',
            'status': int(code) if str(code).isdigit() else code,
            'ms': duration_ms,
        })
        print(f'[webui] {record}', flush=True)

    def do_GET(self) -> None:
        self._req_t0 = time.time()
        # Per-request profile context from cookie (issue #798)
        cookie_profile = get_profile_cookie(self)
        if cookie_profile:
            set_request_profile(cookie_profile)
        parsed = urlparse(self.path)
        # Per-request workspace context (space isolation)
        _setup_workspace_from_request(self, parsed)
        try:
            if not check_auth(self, parsed): return
            result = handle_get(self, parsed)
            if result is False:
                return j(self, {'error': 'not found'}, status=404)
        except Exception as e:
            print(f'[webui] ERROR {self.command} {self.path}\n' + traceback.format_exc(), flush=True)
            return j(self, {'error': 'Internal server error'}, status=500)
        finally:
            clear_request_profile()
            _teardown_workspace_context()

    def _handle_write(self, route_func) -> None:
        self._req_t0 = time.time()
        # Per-request profile context from cookie (issue #798)
        cookie_profile = get_profile_cookie(self)
        if cookie_profile:
            set_request_profile(cookie_profile)
        parsed = urlparse(self.path)
        # Per-request workspace context (space isolation)
        _setup_workspace_from_request(self, parsed)
        try:
            if not check_auth(self, parsed): return
            result = route_func(self, parsed)
            if result is False:
                return j(self, {'error': 'not found'}, status=404)
        except Exception as e:
            print(f'[webui] ERROR {self.command} {self.path}\n' + traceback.format_exc(), flush=True)
            return j(self, {'error': 'Internal server error'}, status=500)
        finally:
            clear_request_profile()
            _teardown_workspace_context()

    def do_POST(self) -> None:
        self._handle_write(handle_post)

    def do_PATCH(self) -> None:
        self._handle_write(handle_patch)

    def do_DELETE(self) -> None:
        self._handle_write(handle_delete)


def _raise_fd_soft_limit(target: int = 4096) -> dict:
    """Best-effort raise of RLIMIT_NOFILE for persistent WebUI hosts.

    macOS launchd jobs often start with a 256 soft limit. If a future FD leak
    regresses, that low ceiling turns a leak into a hard HTTP wedge quickly.
    Raising the soft limit does not hide leaks; it buys enough headroom for
    diagnostics and watchdog recovery.
    """
    if resource is None:
        return {"status": "unsupported"}
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception as exc:
        return {"status": "error", "error": str(exc)}

    # On Unix, RLIM_INFINITY is commonly a large int; keep the logic explicit
    # so tests can use ordinary integers without depending on platform values.
    desired = int(target)
    if hard not in (-1, getattr(resource, "RLIM_INFINITY", object())):
        desired = min(desired, int(hard))
    if soft >= desired:
        return {"status": "unchanged", "soft": soft, "hard": hard}
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (desired, hard))
    except Exception as exc:
        return {"status": "error", "soft": soft, "hard": hard, "error": str(exc)}
    return {"status": "raised", "soft": desired, "hard": hard, "previous_soft": soft}


def _start_cron_ticker() -> None:
    """Background thread that fires cron jobs + periodic housekeeping.

    Runs cron.scheduler.tick() every 60s (executes due jobs), plus
    lightweight housekeeping on a coarser cadence (image/document cache
    cleanup, expired-paste sweep).  This replaces the gateway's built-in
    cron ticker for setups that run the WebUI without a gateway.
    """
    TICK_INTERVAL = 60       # seconds — matches gateway default

    # How many ticks between housekeeping passes (60 ticks × 60s = 1h)
    HOUSEKEEPING_EVERY = 60

    tick_count = 0
    while True:
        try:
            # Fire due cron jobs (lazy import — hermetic, avoids module-level side effects)
            from cron.scheduler import tick as cron_tick
            cron_tick(verbose=False)
        except Exception:
            pass

        tick_count += 1

        if tick_count % HOUSEKEEPING_EVERY == 0:
            # Image/document cache cleanup — once per hour
            try:
                from gateway.platforms.base import cleanup_image_cache, cleanup_document_cache
                removed = cleanup_image_cache(max_age_hours=24)
                if removed:
                    logger.info("Cron ticker: removed %d stale image cache file(s)", removed)
            except Exception:
                pass
            try:
                removed = cleanup_document_cache(max_age_hours=24)
                if removed:
                    logger.info("Cron ticker: removed %d stale document cache file(s)", removed)
            except Exception:
                pass

            # Expired paste sweep — once per hour
            try:
                from cli.debug import _sweep_expired_pastes
                deleted, remaining = _sweep_expired_pastes()
                if deleted:
                    logger.info("Cron ticker: swept %d expired paste(s), %d pending", deleted, remaining)
            except Exception:
                pass

        time.sleep(TICK_INTERVAL)


def main() -> None:
    from web.api.config import print_startup_config, verify_sidekick_imports, _SIDEKICK_FOUND

    log_path = _resolve_log_file()
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    _log_fp = open(log_path, "a", encoding="utf-8")
    sys.stdout = _Tee(sys.stdout, _log_fp)
    sys.stderr = _Tee(sys.stderr, _log_fp)
    print_startup_config()

    fd_limit = _raise_fd_soft_limit()
    if fd_limit.get("status") == "raised":
        print(
            f"[ok] Raised file descriptor soft limit "
            f"{fd_limit.get('previous_soft')} -> {fd_limit.get('soft')}",
            flush=True,
        )
    elif fd_limit.get("status") == "error":
        print(f"[!!] WARNING: Could not raise file descriptor limit: {fd_limit.get('error')}", flush=True)

    # Fix sensitive file permissions before doing anything else
    fix_credential_permissions()

    # ── #1558 startup self-heal ─────────────────────────────────────────
    # If a previous process wrote a session JSON with fewer messages than
    # its .bak (the data-loss shape #1558 produced), restore from the .bak.
    # Safe to run unconditionally — a clean install is a no-op.
    try:
        from web.api.models import _active_state_db_path
        from web.api.session_recovery import recover_all_sessions_on_startup
        result = recover_all_sessions_on_startup(
            SESSION_DIR,
            rebuild_index=True,
            state_db_path=_active_state_db_path(),
        )
        if result.get("restored"):
            print(f"[recovery] Restored {result['restored']}/{result['scanned']} sessions from .bak (see #1558).", flush=True)
    except Exception as exc:
        # Recovery is best-effort; never block server startup.
        print(f"[recovery] startup recovery failed: {exc}", flush=True)

    within_container = False
    # Check for the "/.within_container" file to determine if we're running inside a container; this file is created in the Dockerfile
    try:
        with open('/.within_container', 'r') as f:
            within_container = True
    except FileNotFoundError:
        pass

    if within_container:
        print('[ok] Running within container.', flush=True)

    # Security: warn if binding non-loopback without authentication
    from web.api.auth import is_auth_enabled
    if HOST not in ('127.0.0.1', '::1', 'localhost') and not is_auth_enabled():
        print(f'[!!] WARNING: Binding to {HOST} with NO PASSWORD SET.', flush=True)
        print(f'     Anyone on the network can access your filesystem and agent.', flush=True)
        print(f'     Set a password via Settings or HERMES_WEBUI_PASSWORD env var.', flush=True)
        print(f'     To suppress: bind to 127.0.0.1 or set a password.', flush=True)
        if within_container:
            print(f'     Note: You are running within a container, must bind to 0.0.0.0 (IPv4) or :: (IPv6) to publish the port.', flush=True)
    elif not is_auth_enabled():
        print(f'  [tip] No password set. Any process on this machine can read sessions', flush=True)
        print(f'        and memory via the local API. Set HERMES_WEBUI_PASSWORD to', flush=True)
        print(f'        enable authentication.', flush=True)

    ok, missing, errors = verify_sidekick_imports()
    if not ok and _SIDEKICK_FOUND:
        print(f'[!!] Warning: Nova agent found but missing modules: {missing}', flush=True)
        for mod, err in errors.items():
            print(f'     {mod}: {err}', flush=True)
        print('     Attempting to install missing dependencies from agent requirements.txt...', flush=True)
        auto_install_agent_deps()
        ok, missing, errors = verify_sidekick_imports()
        if not ok:
            print(f'[!!] Still missing after install attempt: {missing}', flush=True)
            for mod, err in errors.items():
                print(f'     {mod}: {err}', flush=True)
            print('     Agent features may not work correctly.', flush=True)
        else:
            print('[ok] Agent dependencies installed successfully.', flush=True)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)

    # ── Agents Registry initialisieren ─────────────────────────────────────
    from web.api.agents import init_agents_db
    init_agents_db(STATE_DIR, SESSION_DIR)

    # ── Agent Workspaces initialisieren ────────────────────────────────────
    try:
        from web.api.agents import list_activated_agents
        from web.api.agent_workspace import init_workspaces_for_agents
        activated = list_activated_agents()
        slugs = [a["slug"] for a in activated]
        if slugs:
            workspaces = init_workspaces_for_agents(slugs)
            print(f"  Agent Workspaces: {len(workspaces)} dirs [ok]", flush=True)
    except Exception as e:
        print(f"  [!!] Agent Workspace init: {e}", flush=True)

    # Start the gateway session watcher for real-time SSE updates
    try:
        from web.api.gateway_watcher import start_watcher
        start_watcher()
    except Exception as e:
        print(f'[!!] WARNING: Gateway watcher failed to start: {e}', flush=True)

    # Start the cron ticker background thread (fires due jobs + housekeeping)
    try:
        t = threading.Thread(target=_start_cron_ticker, daemon=True, name='cron-ticker')
        t.start()
        print('  Cron ticker: started [ok]', flush=True)
    except Exception as e:
        print(f'[!!] WARNING: Cron ticker failed to start: {e}', flush=True)

    httpd = QuietHTTPServer((HOST, PORT), Handler)

    # ── TLS/HTTPS setup (optional) ─────────────────────────────────────────
    from web.api.config import TLS_ENABLED, TLS_CERT, TLS_KEY
    scheme = 'https' if TLS_ENABLED else 'http'
    if TLS_ENABLED:
        try:
            import ssl
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(TLS_CERT, TLS_KEY)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            print(f'  TLS enabled: cert={TLS_CERT}, key={TLS_KEY}', flush=True)
        except Exception as e:
            print(f'[!!] WARNING: TLS setup failed ({e}), falling back to HTTP', flush=True)
            scheme = 'http'

        print(f'  Sidekick listening on {scheme}://{HOST}:{PORT}', flush=True)
        if HOST in ('127.0.0.1', '::1') or within_container:
            print(f'  Remote access: ssh -N -L {PORT}:127.0.0.1:{PORT} <user>@<your-server>', flush=True)
        print(f'  Then open:     {scheme}://localhost:{PORT}', flush=True)
        print('', flush=True)
    _transient_winerrors = (10048, 10053, 10052, 10058, 10054)
    # Wrap serve_forever in a resilient accept loop that survives
    # transient socket errors (common on Windows -- WinError 10048/10022/10054).
    # Without this, an OSError in the accept() loop crashes the entire server.
    _server_ok = True
    while _server_ok:
        try:
            httpd.serve_forever()
            _server_ok = False  # graceful shutdown via KeyboardInterrupt
        except KeyboardInterrupt:
            print('', flush=True)
            print('[shutdown] KeyboardInterrupt received, shutting down.', flush=True)
            _server_ok = False
        except OSError as exc:
            err = getattr(exc, 'winerror', None) or getattr(exc, 'errno', None)
            print(f'[socket] Accept-loop OSError (winerror={err}): {exc}', flush=True)
            # If bind failed (port in use), do NOT restart -- propagate the error
            if err in (10048,):  # WSAEADDRINUSE
                print(f'[socket] Port {PORT} is already in use. Cannot bind.', flush=True)
                _server_ok = False
                raise  # propagate so the batchfile sees non-zero exit
            # Transient errors (ECONNRESET, WSAECONNABORTED, EPIPE, etc.) -- log and resume
            import time as _t
            print(f'[socket] Transient error (winerror={err}) -- resuming accept loop after 1s...', flush=True)
            _t.sleep(1)
        except Exception as exc:
            err_win = getattr(exc, 'winerror', None)
            # Transient Windows errors that should not kill the server
            if err_win in _transient_winerrors:
                print(f'[socket] Transient Exception ({err_win}) -- resuming...', flush=True)
                import time as _t
                _t.sleep(1)
                continue
            print(f'[fatal] Unhandled error in accept loop: {type(exc).__name__}: {exc}', flush=True)
            import traceback as _tb
            _tb.print_exc()
            _server_ok = False
            raise

    # Shutdown cleanup
    try:
        from web.api.gateway_watcher import stop_watcher
        stop_watcher()
    except Exception:
        logger.debug("Failed to stop gateway watcher during shutdown")

if __name__ == '__main__':
    main()
