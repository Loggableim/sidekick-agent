"""
Command Stream — per-session live output capture for agent terminal() calls.

Stores output chunks (stdout/stderr) in a queue per session, consumed by the
SSE endpoint at GET /api/terminal/stream?session_id=XYZ.

Architecture:
  tool callback (streaming.py)
       │  tool.started(name='terminal') → start_command_stream(sid, command)
       │  tool.callback  → write_stdout(sid, chunk)
       │  tool.completed(name='terminal') → end_command_stream(sid, exit_code)
       ▼
  CommandStreamSession (this module)
       │  queue.Queue of (event_type, data_dict)
       ▼
  SSE endpoint (routes.py → streaming._handle_terminal_stream)
       │  EventSource reads queue, writes SSE events
       ▼
  browser TerminalStream (terminal.js)
       │  xterm.term.write(data)
       ▼
  Terminal panel viewport
"""

from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# ── Per-session command stream registry ──────────────────────────────────
# session_id → CommandStreamSession
_COMMAND_STREAMS: dict[str, "CommandStreamSession"] = {}
_LOCK = threading.RLock()

# Default max queue size to avoid unbounded memory growth on a long-lived
# streaming session that nobody is reading.
_DEFAULT_QUEUE_MAXSIZE = 5000


@dataclass
class CommandStreamSession:
    """Tracks live output for one agent terminal() invocation.

    The streaming.py tool callback writes stdout/stderr chunks via
    write_stdout() / write_stderr().  The SSE endpoint reads from
    ``output`` via get() and sends SSE events to the browser.
    """

    session_id: str
    command: str = ""
    output: queue.Queue = field(
        default_factory=lambda: queue.Queue(maxsize=_DEFAULT_QUEUE_MAXSIZE)
    )
    exit_code: Optional[int] = None
    finished: threading.Event = field(default_factory=threading.Event)
    _closed: bool = False

    @property
    def is_alive(self) -> bool:
        return not self._closed and not self.finished.is_set()

    def put(self, event: str, data: dict) -> None:
        """Push one SSE event onto the queue (non-blocking drop on full)."""
        if self._closed:
            return
        try:
            self.output.put_nowait((event, data))
        except queue.Full:
            # Drop oldest to keep the stream responsive.
            try:
                self.output.get_nowait()
            except queue.Empty:
                pass
            try:
                self.output.put_nowait((event, data))
            except queue.Full:
                pass

    def write_stdout(self, text: str) -> None:
        """Send a stdout chunk."""
        if text:
            self.put("stdout", {"data": text})

    def write_stderr(self, text: str) -> None:
        """Send a stderr chunk."""
        if text:
            self.put("stderr", {"data": text})

    def finish(self, exit_code: int = 0) -> None:
        """Mark the command stream as finished."""
        self.exit_code = exit_code
        self.finished.set()
        self.put("exit", {"code": exit_code})

    def close(self) -> None:
        """Immediately close the stream (e.g. on session switch)."""
        self._closed = True
        self.finished.set()


# ── Public API ──────────────────────────────────────────────────────────


def start_command_stream(
    session_id: str, command: str = ""
) -> CommandStreamSession:
    """Create or reset a command stream for the given session."""
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    with _LOCK:
        existing = _COMMAND_STREAMS.get(sid)
        if existing and existing.is_alive:
            existing.close()
        stream = CommandStreamSession(session_id=sid, command=command)
        _COMMAND_STREAMS[sid] = stream
        return stream


def get_command_stream(session_id: str) -> Optional[CommandStreamSession]:
    """Return the active command stream for a session, or None."""
    with _LOCK:
        stream = _COMMAND_STREAMS.get(str(session_id or ""))
        if stream and not stream.finished.is_set():
            return stream
        return None


def end_command_stream(
    session_id: str, exit_code: int = 0
) -> Optional[CommandStreamSession]:
    """Finish the command stream for a session and return it."""
    sid = str(session_id or "").strip()
    with _LOCK:
        stream = _COMMAND_STREAMS.get(sid)
        if stream:
            stream.finish(exit_code)
        return stream


def close_command_stream(session_id: str) -> None:
    """Close (abort) the command stream for a session."""
    sid = str(session_id or "").strip()
    with _LOCK:
        stream = _COMMAND_STREAMS.pop(sid, None)
        if stream:
            stream.close()


def write_command_stdout(session_id: str, text: str) -> None:
    """Write a stdout chunk to the session's command stream (no-op if none)."""
    stream = get_command_stream(session_id)
    if stream:
        stream.write_stdout(text)


def write_command_stderr(session_id: str, text: str) -> None:
    """Write a stderr chunk to the session's command stream (no-op if none)."""
    stream = get_command_stream(session_id)
    if stream:
        stream.write_stderr(text)


# ── Cleanup ─────────────────────────────────────────────────────────────


def cleanup_session(session_id: str) -> None:
    """Remove all command stream state for a session."""
    sid = str(session_id or "").strip()
    with _LOCK:
        stream = _COMMAND_STREAMS.pop(sid, None)
        if stream:
            stream.close()
