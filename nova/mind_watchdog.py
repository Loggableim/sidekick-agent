"""Lease-bound liveness watchdog for the host-owned Nova Mind process.

The watchdog is deliberately small and fail-closed: only the dashboard ticker
that owns the durable supervision lease may start the fixed Nova entrypoint.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Any

WATCHDOG_WINDOW_SECONDS = 300
MAX_CRASHES = 3
STATUS_STALE_SECONDS = 180


@dataclass(frozen=True)
class WatchdogResult:
    status: str
    pid: int | None = None
    restarted: bool = False
    crash_count: int = 0


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    os.replace(temp, path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _default_pid_alive(pid: int, target: Path) -> bool:
    """Require a live process whose command line names the Nova Mind target."""
    try:
        import psutil  # type: ignore

        process = psutil.Process(pid)
        if not process.is_running():
            return False
        command = " ".join(process.cmdline()).lower()
        return target.name.lower() in command
    except Exception:
        return False


def _fresh_status(status_path: Path, now: float) -> bool:
    try:
        if now - status_path.stat().st_mtime > STATUS_STALE_SECONDS:
            return False
        payload = _read_json(status_path)
        return payload.get("status") == "alive"
    except OSError:
        return False


def check_and_recover(
    *,
    home: Path,
    lease_owned: bool,
    now: float | None = None,
    pid_alive: Callable[[int, Path], bool] | None = None,
    popen: Callable[..., Any] | None = None,
) -> WatchdogResult:
    """Check Nova Mind and restart it only while holding the supervision lease."""
    now = float(time.time() if now is None else now)
    home = Path(home)
    nova_root = home / "spaces" / "nova"
    pid_path = nova_root / "nova_data" / "runtime" / "nova_mind.pid.json"
    status_path = nova_root / "nova-site" / "nova-status.json"
    state_path = home / "state" / "nova-mind-watchdog.json"
    target = nova_root / "nova_mind.py"
    pid = _read_json(pid_path).get("pid")
    pid = pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else None
    alive = bool(pid and (pid_alive or _default_pid_alive)(pid, target))
    if alive and _fresh_status(status_path, now):
        return WatchdogResult("healthy", pid=pid)
    if not lease_owned:
        return WatchdogResult("not_lease_holder", pid=pid)
    if not target.is_file():
        return WatchdogResult("missing_target", pid=pid)

    state = _read_json(state_path)
    history = state.get("crash_timestamps", [])
    if not isinstance(history, list):
        history = []
    timestamps = [float(item) for item in history if isinstance(item, (int, float)) and now - float(item) <= WATCHDOG_WINDOW_SECONDS]
    if len(timestamps) >= MAX_CRASHES:
        _atomic_json(state_path, {"crash_timestamps": timestamps[-MAX_CRASHES:], "status": "escalated"})
        return WatchdogResult("escalated", pid=pid, crash_count=len(timestamps))

    launcher = popen or subprocess.Popen
    command = [sys.executable, str(target)]
    process = launcher(command, cwd=str(nova_root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    new_pid = int(getattr(process, "pid", 0) or 0) or None
    timestamps.append(now)
    _atomic_json(state_path, {"crash_timestamps": timestamps[-MAX_CRASHES:], "status": "restarted", "pid": new_pid})
    if new_pid:
        _atomic_json(pid_path, {"pid": new_pid, "started_at": datetime.now(timezone.utc).isoformat()})
    return WatchdogResult("restarted", pid=new_pid, restarted=True, crash_count=len(timestamps))
