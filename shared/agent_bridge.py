from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class BridgeResult:
    ok: bool
    reply: str
    backend: str
    error: str | None = None


def _detect_legacy_sidekick() -> str | None:
    return shutil.which("sidekick")


def _bridge_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("SIDEKICK_YOLO_MODE", "1")
    env.setdefault("SIDEKICK_ACCEPT_HOOKS", "1")
    return env


def run_assistant_once(prompt: str, timeout_seconds: int = 45) -> BridgeResult:
    command = _detect_legacy_sidekick()
    if not command:
        return BridgeResult(
            ok=False,
            reply="Legacy Sidekick command was not found. The monorepo web surface is running, but the in-repo agent runtime is not ported yet.",
            backend="none",
            error="legacy sidekick command not found",
        )

    try:
        result = subprocess.run(
            [command, "-z", prompt, "--ignore-user-config", "--ignore-rules"],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout_seconds,
            cwd=str(Path.cwd()),
            env=_bridge_env(),
        )
    except Exception as exc:
        return BridgeResult(
            ok=False,
            reply="Assistant bridge invocation failed before a reply was produced.",
            backend=command,
            error=str(exc),
        )

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode == 0 and stdout:
        return BridgeResult(ok=True, reply=stdout, backend=command)

    if stderr:
        details = stderr
    elif result.returncode:
        details = f"exit code {result.returncode}"
    else:
        details = "empty response"
    return BridgeResult(
        ok=False,
        reply=(
            "Legacy assistant bridge did not return a usable answer. "
            "The standalone repo can persist the conversation state, but the real in-repo agent runtime still needs to be ported."
        ),
        backend=command,
        error=details,
    )
