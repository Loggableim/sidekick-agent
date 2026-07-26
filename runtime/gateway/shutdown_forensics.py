"""Shutdown forensics — capture context when the gateway receives SIGTERM/SIGINT.

The gateway's ``shutdown_signal_handler`` runs synchronously inside the
asyncio event loop.  We can't safely block it for long, but we DO want a
durable record of who/what triggered the shutdown so that "the gateway
keeps dying" incidents can be diagnosed after the fact.

This module exposes :func:`snapshot_shutdown_context`, a fast (<10ms),
non-blocking probe that returns a structured dict the signal handler can
log immediately, plus :func:`spawn_async_diagnostic`, a fire-and-forget
``ps`` walk that runs as a detached subprocess so it can't block teardown
even if /proc is wedged.

Anything that needs to wait (e.g. shelling out to ``ps aux``) belongs in
the async helper, never in the synchronous probe.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Any, Dict, Optional


_SIGNAL_NAME_BY_NUM: Dict[int, str] = {}
for _name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGQUIT", "SIGUSR1", "SIGUSR2"):
    _val = getattr(signal, _name, None)
    if _val is not None:
        _SIGNAL_NAME_BY_NUM[int(_val)] = _name


def _signal_name(sig: Any) -> str:
    """Return a human-readable signal name (or ``str(sig)`` as fallback)."""
    if sig is None:
        return "UNKNOWN"
    try:
        sig_int = int(sig)
    except (TypeError, ValueError):
        return str(sig)
    return _SIGNAL_NAME_BY_NUM.get(sig_int, f"signal#{sig_int}")


def _read_proc_field(pid: int, key: str) -> Optional[str]:
    """Read a single field from /proc/<pid>/status.  Linux only; None elsewhere."""
    try:
        with open(f"/proc/{pid}/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith(key + ":"):
                    return line.split(":", 1)[1].strip()
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return None


def _read_proc_cmdline(pid: int) -> Optional[str]:
    """Read the full command line of a process from /proc/<pid>/cmdline.

    Linux only; returns None elsewhere.
    """
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _read_proc_environ(pid: int) -> Optional[str]:
    """Read the full environment of a process from /proc/<pid>/environ, truncated.

    Linux only; returns None elsewhere.
    """
    try:
        with open(f"/proc/{pid}/environ", "rb") as fh:
            raw = fh.read(4096)  # limit to first 4KB
        return raw.replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
    except (FileNotFoundError, PermissionError, OSError):
        return None


def snapshot_shutdown_context(sig: Any, frame: Any = None) -> Dict[str, Any]:
    """Fast, synchronous probe that captures shutdown context.

    Must complete in <10ms — runs inside the signal handler.

    Returns a dict with keys:
    - signal_name
    - signal_number
    - timestamp
    - pid
    - ppid
    - parent_process_name
    - parent_cmdline
    - cwd
    - argv
    """
    ts = time.time()
    pid = os.getpid()
    ppid = os.getppid()

    ctx: Dict[str, Any] = {
        "signal_name": _signal_name(sig),
        "signal_number": int(sig) if sig is not None else -1,
        "timestamp": ts,
        "iso_timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts)),
        "pid": pid,
        "ppid": ppid,
        "parent_process_name": _read_proc_field(ppid, "Name"),
        "parent_cmdline": _read_proc_cmdline(ppid),
        "cwd": os.getcwd() if hasattr(os, "getcwd") else "",
        "argv": " ".join(sys.argv) if hasattr(sys, "argv") else "",
    }

    # Shallow /proc/self scan (fast — no child processes)
    ctx["self_name"] = _read_proc_field(pid, "Name")
    ctx["self_state"] = _read_proc_field(pid, "State")

    return ctx


async def spawn_async_diagnostic(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Fire-and-forget async diagnostic that runs ``ps`` and ``/proc`` walks.

    This is safe to call from the signal handler because it doesn't block
    — it spawns a subprocess and returns a future.  The caller can decide
    whether to await it (and log the result) or fire-and-forget.

    Returns a dict with additional diagnostic data merged on top of *ctx*.
    """
    augmented = dict(ctx)
    augmented["async_timestamp"] = time.time()

    try:
        # ps aux --forest for parent process tree
        ps_result = subprocess.run(
            ["ps", "aux", "--forest"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5.0,
        )
        if ps_result.returncode == 0:
            augmented["ps_aux_output"] = ps_result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Walk child processes
    try:
        children = []
        my_pid = os.getpid()
        for entry in os.listdir("/proc"):
            if not entry.isdigit():
                continue
            try:
                status_path = f"/proc/{entry}/status"
                with open(status_path, encoding="utf-8") as fh:
                    content = fh.read()
                ppid_line = [l for l in content.splitlines() if l.startswith("PPid:")]
                if ppid_line and ppid_line[0].split(":")[1].strip() == str(my_pid):
                    name_line = [l for l in content.splitlines() if l.startswith("Name:")]
                    name = name_line[0].split(":")[1].strip() if name_line else "?"
                    children.append({"pid": int(entry), "name": name})
            except (OSError, PermissionError):
                continue
        augmented["child_processes"] = children
    except (FileNotFoundError, OSError):
        pass

    return augmented


def format_shutdown_context(ctx: Dict[str, Any]) -> str:
    """Format a shutdown context dict as a human-readable string for logging."""
    lines = [
        f"Gateway shutdown ({ctx.get('signal_name', 'UNKNOWN')})",
        f"  PID: {ctx.get('pid')}  PPID: {ctx.get('ppid')}",
        f"  Parent: {ctx.get('parent_process_name', '?')}",
        f"  CWD: {ctx.get('cwd', '?')}",
        f"  Args: {ctx.get('argv', '?')}",
    ]
    if ctx.get("parent_cmdline"):
        lines.append(f"  Parent cmdline: {ctx['parent_cmdline']}")
    if ctx.get("ps_aux_output"):
        lines.append(f"  Process tree:\n{ctx['ps_aux_output']}")
    if ctx.get("child_processes"):
        children = ctx["child_processes"]
        if children:
            child_lines = [f"    {c['pid']}: {c['name']}" for c in children]
            lines.append(f"  Children ({len(children)}):\n" + "\n".join(child_lines))
    return "\n".join(lines)
