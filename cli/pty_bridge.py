"""PTY bridge for `sidekick dashboard` chat tab.

Wraps a child process behind a pseudo-terminal so its ANSI output can be
streamed to a browser-side terminal emulator (xterm.js) and typed
keystrokes can be fed back in.  The only caller today is the
``/api/pty`` WebSocket endpoint in ``sidekick_cli.web_server``.

Design constraints:

* **Cross-platform.**  On POSIX (Linux/macOS/WSL) we use ``ptyprocess``
  (``fcntl`` + ``termios`` + ``os.openpty``).  On native Windows we use
  ``winpty`` (ConPTY via ``pywinpty``).  The :class:`PtyBridge` API is
  identical on both platforms — callers don't branch on OS.
* **Zero Node dependency on the server side.**  We use pure-Python PTY
  wrappers.  The browser talks to the same ``sidekick --tui`` binary it
  would launch from the CLI, so every TUI feature (slash popover, model
  picker, tool rows, markdown, skin engine, clarify/sudo/approval
  prompts) ships automatically.
* **Byte-safe I/O.**  Reads and writes go through the PTY master fd
  directly — we avoid :class:`ptyprocess.PtyProcessUnicode` because
  streaming ANSI is inherently byte-oriented and UTF-8 boundaries may land
  mid-read.  On Windows, ``winpty`` returns ``str``; we encode to
  ``bytes`` so callers always receive bytes regardless of platform.
"""

from __future__ import annotations

import errno
import os
import select
import signal
import struct
import sys
import time
from typing import Optional, Sequence

# ── Platform detection ───────────────────────────────────────────────────
_IS_WINDOWS = sys.platform.startswith("win")

# ── POSIX imports (fcntl, termios, ptyprocess) ───────────────────────────
fcntl = None
termios = None
ptyprocess = None
_POSIX_PTY_AVAILABLE = False

if not _IS_WINDOWS:
    try:
        import fcntl
        import termios
        import ptyprocess  # type: ignore
        _POSIX_PTY_AVAILABLE = True
    except ImportError:
        pass

# ── Windows imports (winpty / pywinpty) ──────────────────────────────────
winpty = None
_WINPTY_AVAILABLE = False

if _IS_WINDOWS:
    try:
        import winpty  # type: ignore
        _WINPTY_AVAILABLE = True
    except ImportError:
        pass

_PTY_AVAILABLE = _POSIX_PTY_AVAILABLE or _WINPTY_AVAILABLE


__all__ = ["PtyBridge", "PtyUnavailableError"]


class PtyUnavailableError(RuntimeError):
    """Raised when a PTY cannot be created on this platform.

    On POSIX this means ``ptyprocess`` is missing.  On Windows this means
    ``pywinpty`` is not installed.  The dashboard surfaces the message to
    the user as a chat-tab banner.
    """


class PtyBridge:
    """Thin wrapper around a platform PTY process for byte streaming.

    On POSIX: wraps ``ptyprocess.PtyProcess``.
    On Windows: wraps ``winpty.PtyProcess`` (ConPTY).

    Both platforms use the same ``select`` + ``read`` pattern because
    ``winpty.fileno()`` returns a socket fd that ``select.select()`` can
    poll.  No dedicated reader thread needed.

    Not thread-safe.  A single bridge is owned by the WebSocket handler
    that spawned it; the reader runs in an executor thread while writes
    happen on the event-loop thread.

    Public API is identical on both platforms:
    - ``.fd`` — master file descriptor (int)
    - ``.pid`` — child process ID (int)
    - ``.read(timeout)`` — read up to 64 KiB, returns ``bytes``, ``b""``,
      or ``None`` (EOF)
    - ``.write(data: bytes)`` — write raw bytes to child stdin
    - ``.resize(cols, rows)`` — forward terminal resize
    - ``.close()`` — terminate child and close fds
    - ``.is_alive()`` — True if child is still running
    - ``.is_available()`` — classmethod, True if PTY can be spawned
    - ``.spawn(argv, ...)`` — classmethod, spawn a new PTY
    """

    def __init__(self, proc, *, _is_winpty: bool = False):
        self._proc = proc
        self._fd: int = proc.fd
        self._closed = False
        self._is_winpty = _is_winpty

    # -- lifecycle --------------------------------------------------------

    @classmethod
    def is_available(cls) -> bool:
        """True if a PTY can be spawned on this platform."""
        return bool(_PTY_AVAILABLE)

    @classmethod
    def spawn(
        cls,
        argv: Sequence[str],
        *,
        cwd: Optional[str] = None,
        env: Optional[dict] = None,
        cols: int = 80,
        rows: int = 24,
    ) -> "PtyBridge":
        """Spawn ``argv`` behind a new PTY and return a bridge.

        Raises :class:`PtyUnavailableError` if the platform can't host a
        PTY.  Raises :class:`FileNotFoundError` or :class:`OSError` for
        ordinary exec failures (missing binary, bad cwd, etc.).
        """
        if not _PTY_AVAILABLE:
            if _IS_WINDOWS and not _WINPTY_AVAILABLE:
                raise PtyUnavailableError(
                    "Pseudo-terminals are unavailable on this platform. "
                    "Install pywinpty: pip install pywinpty"
                )
            if not _POSIX_PTY_AVAILABLE:
                if ptyprocess is None:
                    raise PtyUnavailableError(
                        "The `ptyprocess` package is missing. "
                        "Install with: pip install ptyprocess "
                        "(or pip install -e '.[pty]')."
                    )
                raise PtyUnavailableError(
                    "Pseudo-terminals are unavailable on this platform."
                )
            raise PtyUnavailableError("Pseudo-terminals are unavailable.")

        # PTY-hosted programs expect TERM to describe the terminal type.
        spawn_env = (os.environ.copy() if env is None else dict(env))
        if not spawn_env.get("TERM"):
            spawn_env["TERM"] = "xterm-256color"

        if _IS_WINDOWS and _WINPTY_AVAILABLE:
            proc = winpty.PtyProcess.spawn(
                list(argv),
                cwd=cwd,
                env=spawn_env,
                dimensions=(rows, cols),
            )
            return cls(proc, _is_winpty=True)
        else:
            proc = ptyprocess.PtyProcess.spawn(  # type: ignore[union-attr]
                list(argv),
                cwd=cwd,
                env=spawn_env,
                dimensions=(rows, cols),
            )
            return cls(proc, _is_winpty=False)

    @property
    def pid(self) -> int:
        return int(self._proc.pid)

    def is_alive(self) -> bool:
        if self._closed:
            return False
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    # -- I/O --------------------------------------------------------------

    def read(self, timeout: float = 0.2) -> Optional[bytes]:
        """Read up to 64 KiB of raw bytes from the PTY master.

        Returns:
            * bytes — zero or more bytes of child output
            * empty bytes (``b""``) — no data available within ``timeout``
            * None — child has exited and the master fd is at EOF

        Never blocks longer than ``timeout`` seconds.  Safe to call after
        :meth:`close`; returns ``None`` in that case.
        """
        if self._closed:
            return None

        try:
            readable, _, _ = select.select([self._fd], [], [], timeout)
        except (OSError, ValueError):
            return None

        if not readable:
            return b""

        if self._is_winpty:
            return self._read_winpty()
        else:
            return self._read_posix()

    def _read_posix(self) -> Optional[bytes]:
        """POSIX: os.read on the master fd."""
        try:
            data = os.read(self._fd, 65536)
        except OSError as exc:
            if exc.errno in {errno.EIO, errno.EBADF}:
                return None
            raise
        if not data:
            return None
        return data

    def _read_winpty(self) -> Optional[bytes]:
        """Windows: proc.read() returns str; encode to bytes."""
        try:
            data = self._proc.read(65536)
        except EOFError:
            return None
        except Exception:
            return None
        if not data:
            return None
        return data.encode("utf-8", errors="replace")

    def write(self, data: bytes) -> None:
        """Write raw bytes to the PTY master (i.e. the child's stdin)."""
        if self._closed or not data:
            return

        if self._is_winpty:
            text = data.decode("utf-8", errors="replace")
            try:
                self._proc.write(text)
            except (OSError, EOFError):
                pass
            return

        # POSIX: os.write with short-write loop
        view = memoryview(data)
        while view:
            try:
                n = os.write(self._fd, view)
            except OSError as exc:
                if exc.errno in {errno.EIO, errno.EBADF, errno.EPIPE}:
                    return
                raise
            if n <= 0:
                return
            view = view[n:]

    def resize(self, cols: int, rows: int) -> None:
        """Forward a terminal resize to the child."""
        if self._closed:
            return

        if self._is_winpty:
            try:
                self._proc.setwinsize(max(1, rows), max(1, cols))
            except Exception:
                pass
            return

        # POSIX: TIOCSWINSZ ioctl
        winsize = struct.pack("HHHH", max(1, rows), max(1, cols), 0, 0)
        try:
            fcntl.ioctl(self._fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass

    # -- teardown ---------------------------------------------------------

    def close(self) -> None:
        """Terminate the child and close fds.

        Idempotent.  Reaping the child is important so we don't leak
        zombies across the lifetime of the dashboard process.
        """
        if self._closed:
            return
        self._closed = True

        if self._is_winpty:
            try:
                self._proc.terminate(force=True)
            except Exception:
                pass
            try:
                self._proc.close(force=True)
            except Exception:
                pass
            return

        # POSIX: SIGHUP → SIGTERM → SIGKILL escalation
        for sig in (signal.SIGHUP, signal.SIGTERM, signal.SIGKILL):
            if not self._proc.isalive():
                break
            try:
                self._proc.kill(sig)
            except Exception:
                pass
            deadline = time.monotonic() + 0.5
            while self._proc.isalive() and time.monotonic() < deadline:
                time.sleep(0.02)

        try:
            self._proc.close(force=True)
        except Exception:
            pass

    # Context-manager sugar — handy in tests and ad-hoc scripts.
    def __enter__(self) -> "PtyBridge":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
