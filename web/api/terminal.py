"""Embedded workspace terminal support for Sidekick.

The terminal is intentionally independent from the agent execution path.  It
starts a shell with an explicit cwd/env per process and never mutates
process-global os.environ, which avoids expanding the session-env race tracked
in the agent execution layer.

Cross-platform: POSIX (Linux/macOS/WSL) uses ``os.openpty()`` + ``fcntl`` +
``termios``.  Native Windows uses ``winpty`` (ConPTY via ``pywinpty``).

Both platforms use the same ``select`` + ``read`` pattern because
``winpty.fileno()`` returns a socket fd that ``select.select()`` can poll.
"""

from __future__ import annotations

import codecs
import errno
import os
import platform
import queue
import select
import shutil
import signal
import struct
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

# ── Platform detection ──────────────────────────────────────────────────
_IS_WINDOWS = platform.system() == "Windows"

# ── POSIX-only imports ──────────────────────────────────────────────────
fcntl = None
termios = None
_POSIX_AVAILABLE = False

if not _IS_WINDOWS:
    try:
        import fcntl
        import termios
        _POSIX_AVAILABLE = True
    except ImportError:
        pass

# ── Windows imports (winpty / pywinpty) ─────────────────────────────────
winpty = None
_WINPTY_AVAILABLE = False

if _IS_WINDOWS:
    try:
        import winpty  # type: ignore
        _WINPTY_AVAILABLE = True
    except ImportError:
        pass


def _set_nonblocking(fd: int) -> None:
    if fcntl is None:
        raise RuntimeError("fcntl not available (Windows)")
    flags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)


def _winsize(rows: int, cols: int) -> bytes:
    if termios is None:
        raise RuntimeError("termios not available (Windows)")
    rows = max(8, min(int(rows or 24), 80))
    cols = max(20, min(int(cols or 80), 240))
    return struct.pack("HHHH", rows, cols, 0, 0)


@dataclass
class TerminalSession:
    session_id: str
    workspace: str
    proc: "subprocess.Popen | object"  # subprocess.Popen (POSIX) or winpty.PtyProcess (Windows)
    master_fd: int
    rows: int = 24
    cols: int = 80
    output: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=2000))
    closed: threading.Event = field(default_factory=threading.Event)
    reader: threading.Thread | None = None
    _is_winpty: bool = False

    def is_alive(self) -> bool:
        if self.closed.is_set():
            return False
        if self._is_winpty:
            try:
                return bool(self.proc.isalive())  # type: ignore[union-attr]
            except Exception:
                return False
        return self.proc.poll() is None  # type: ignore[union-attr]

    def put_output(self, event: str, payload: dict) -> None:
        try:
            self.output.put_nowait((event, payload))
        except queue.Full:
            try:
                self.output.get_nowait()
            except queue.Empty:
                pass
            try:
                self.output.put_nowait((event, payload))
            except queue.Full:
                pass


_TERMINALS: dict[str, TerminalSession] = {}
_LOCK = threading.RLock()


def _decode_terminal_output(decoder, data: bytes) -> str:
    """Decode PTY bytes without stripping terminal control sequences."""
    return decoder.decode(data)


def _shell_path() -> str:
    shell = os.environ.get("SHELL") or ""
    if shell and Path(shell).exists():
        return shell
    if _IS_WINDOWS:
        ps = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
        if ps:
            return ps
        return shutil.which("cmd.exe") or "cmd.exe"
    return shutil.which("zsh") or shutil.which("bash") or shutil.which("sh") or "/bin/sh"


def _shell_argv(shell: str) -> list[str]:
    name = Path(shell).name.lower()
    if name in {"zsh", "bash", "sh"}:
        return [shell, "-i"]
    if name in {"powershell.exe", "pwsh.exe"}:
        return [shell, "-NoLogo", "-NoExit"]
    if name == "cmd.exe":
        return [shell]
    return [shell]


# ── Reader loop (cross-platform: select + read) ─────────────────────────


def _reader_loop(term: TerminalSession) -> None:
    """Read PTY output via select + read and push to the output queue.

    Works on both POSIX and Windows because winpty.fileno() returns a
    socket fd that select.select() can poll.
    """
    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    try:
        while not term.closed.is_set():
            if not term.is_alive():
                break
            try:
                readable, _, _ = select.select([term.master_fd], [], [], 0.25)
            except (OSError, ValueError):
                break
            if not readable:
                continue

            if term._is_winpty:
                # winpty: proc.read() returns str
                try:
                    data = term.proc.read(8192)  # type: ignore[union-attr]
                except EOFError:
                    break
                except Exception:
                    break
                if not data:
                    break
                raw = data.encode("utf-8", errors="replace")
            else:
                # POSIX: os.read on master fd
                try:
                    raw = os.read(term.master_fd, 8192)
                except OSError as exc:
                    if exc.errno in (errno.EIO, errno.EBADF):
                        break
                    raise
                if not raw:
                    break

            text = _decode_terminal_output(decoder, raw)
            if text:
                term.put_output("output", {"text": text})
    except Exception as exc:
        term.put_output("terminal_error", {"error": str(exc)})
    finally:
        term.closed.set()
        if term._is_winpty:
            exit_code = None
            try:
                exit_code = term.proc.wait()  # type: ignore[union-attr]
            except Exception:
                pass
        else:
            exit_code = term.proc.poll()  # type: ignore[union-attr]
        term.put_output("terminal_closed", {"exit_code": exit_code})


# ── Resize ───────────────────────────────────────────────────────────────


def _set_size(term: TerminalSession, rows: int, cols: int) -> None:
    term.rows = max(8, min(int(rows or term.rows or 24), 80))
    term.cols = max(20, min(int(cols or term.cols or 80), 240))

    if term._is_winpty:
        try:
            term.proc.setwinsize(term.rows, term.cols)  # type: ignore[union-attr]
        except Exception:
            pass
        return

    # POSIX: TIOCSWINSZ ioctl + SIGWINCH
    if fcntl is not None and termios is not None:
        try:
            fcntl.ioctl(term.master_fd, termios.TIOCSWINSZ, _winsize(term.rows, term.cols))
        except OSError:
            pass
    try:
        if term.proc.poll() is None:  # type: ignore[union-attr]
            os.killpg(term.proc.pid, signal.SIGWINCH)  # type: ignore[union-attr]
    except (OSError, ProcessLookupError):
        pass


# ── Public API ───────────────────────────────────────────────────────────


def start_terminal(
    session_id: str,
    workspace: Path,
    rows: int = 24,
    cols: int = 80,
    restart: bool = False,
) -> TerminalSession:
    """Start or return the embedded terminal for a WebUI session."""
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    cwd = str(Path(workspace).expanduser().resolve())
    if not Path(cwd).is_dir():
        raise ValueError("workspace is not a directory")

    # Check platform availability
    if _IS_WINDOWS and not _WINPTY_AVAILABLE:
        raise RuntimeError(
            "The embedded workspace terminal requires a POSIX pseudo-terminal "
            "(PTY), which is not available on native Windows Python.  "
            "Install pywinpty: pip install pywinpty"
        )
    if not _IS_WINDOWS and not _POSIX_AVAILABLE:
        raise RuntimeError(
            "The embedded workspace terminal requires a POSIX pseudo-terminal "
            "(PTY), which is not available on this platform."
        )

    with _LOCK:
        current = _TERMINALS.get(sid)
        if current and current.is_alive() and not restart and current.workspace == cwd:
            _set_size(current, rows, cols)
            return current
        if current:
            close_terminal(sid)

        # Build a safe env: allowlist common shell vars, strip API keys and secrets.
        _SAFE_ENV_KEYS = {
            "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL",
            "LC_CTYPE", "LC_MESSAGES", "LANGUAGE", "TZ", "TMPDIR", "TEMP",
            "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
            # Windows-specific
            "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SystemRoot",
            "SystemDrive", "HOMEDRIVE", "HOMEPATH", "COMSPEC", "PATHEXT",
            "PROMPT", "ALLUSERSPROFILE", "PROGRAMFILES", "PROGRAMFILES(X86)",
            "PROGRAMDATA", "PUBLIC", "WINDIR",
        }
        env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
        env.update(
            {
                "TERM": "xterm-256color",
                "COLORTERM": "truecolor",
                "COLUMNS": str(cols),
                "LINES": str(rows),
                "PWD": cwd,
                "SIDEKICK_WEBUI_TERMINAL": "1",
                "HERMES_WEBUI_TERMINAL": "1",
            }
        )

        shell = _shell_path()

        if _IS_WINDOWS and _WINPTY_AVAILABLE:
            # Windows: use winpty (ConPTY)
            proc = winpty.PtyProcess.spawn(
                _shell_argv(shell),
                cwd=cwd,
                env=env,
                dimensions=(rows, cols),
            )
            term = TerminalSession(
                session_id=sid,
                workspace=cwd,
                proc=proc,
                master_fd=proc.fd,
                rows=rows,
                cols=cols,
                _is_winpty=True,
            )
            term.reader = threading.Thread(
                target=_reader_loop, args=(term,), daemon=True
            )
            term.reader.start()
            _TERMINALS[sid] = term
            return term

        # POSIX: os.openpty() + subprocess.Popen
        master_fd, slave_fd = os.openpty()
        proc = subprocess.Popen(
            _shell_argv(shell),
            cwd=cwd,
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            start_new_session=True,
        )
        os.close(slave_fd)
        _set_nonblocking(master_fd)

        term = TerminalSession(
            session_id=sid,
            workspace=cwd,
            proc=proc,
            master_fd=master_fd,
            rows=rows,
            cols=cols,
            _is_winpty=False,
        )
        _set_size(term, rows, cols)
        term.reader = threading.Thread(
            target=_reader_loop, args=(term,), daemon=True
        )
        term.reader.start()
        _TERMINALS[sid] = term
        return term


def get_terminal(session_id: str) -> TerminalSession | None:
    with _LOCK:
        term = _TERMINALS.get(str(session_id or ""))
        if term and term.is_alive():
            return term
        return term


def write_terminal(session_id: str, data: str) -> None:
    term = get_terminal(session_id)
    if not term or not term.is_alive():
        raise KeyError("terminal not running")

    if term._is_winpty:
        try:
            term.proc.write(str(data or ""))  # type: ignore[union-attr]
        except (OSError, EOFError):
            pass
        return

    # POSIX: os.write to master fd
    os.write(term.master_fd, str(data or "").encode("utf-8", errors="replace"))


def resize_terminal(session_id: str, rows: int, cols: int) -> None:
    term = get_terminal(session_id)
    if not term:
        raise KeyError("terminal not running")
    _set_size(term, rows, cols)


def close_terminal(session_id: str) -> bool:
    sid = str(session_id or "")
    with _LOCK:
        term = _TERMINALS.pop(sid, None)
    if not term:
        return False
    term.closed.set()

    if term._is_winpty:
        try:
            term.proc.terminate(force=True)  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            term.proc.close(force=True)  # type: ignore[union-attr]
        except Exception:
            pass
        return True

    # POSIX: SIGHUP → wait → SIGKILL escalation
    try:
        if term.proc.poll() is None:  # type: ignore[union-attr]
            try:
                os.killpg(term.proc.pid, signal.SIGHUP)  # type: ignore[union-attr]
            except ProcessLookupError:
                pass
            try:
                term.proc.wait(timeout=1.5)  # type: ignore[union-attr]
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(term.proc.pid, signal.SIGKILL)  # type: ignore[union-attr]
                except ProcessLookupError:
                    pass
    finally:
        try:
            os.close(term.master_fd)
        except OSError:
            pass
    return True
