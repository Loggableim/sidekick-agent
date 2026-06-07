from __future__ import annotations

import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from shared.paths import sidekick_home, state_dir

WEB_HOST_ENVS = ("SIDEKICK_WEBUI_HOST", "HERMES_WEBUI_HOST")
WEB_PORT_ENVS = ("SIDEKICK_WEBUI_PORT", "HERMES_WEBUI_PORT")
WEB_STATE_DIR_ENVS = ("SIDEKICK_WEBUI_STATE_DIR", "HERMES_WEBUI_STATE_DIR")
WEB_AGENT_DIR_ENVS = ("SIDEKICK_WEBUI_AGENT_DIR", "HERMES_WEBUI_AGENT_DIR")
WEB_PYTHON_ENVS = ("SIDEKICK_WEBUI_PYTHON", "HERMES_WEBUI_PYTHON")


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


@dataclass(frozen=True)
class WebRuntimeConfig:
    host: str
    port: int
    state_dir: Path
    agent_dir: Path | None
    python_exe: str


def web_state_dir() -> Path:
    configured = _env_first(*WEB_STATE_DIR_ENVS)
    if configured:
        return Path(configured).expanduser().resolve()
    return (state_dir() / "webui").resolve()


def discover_agent_dir(repo_root: Path) -> Path | None:
    candidates: list[Path] = []

    explicit = _env_first(*WEB_AGENT_DIR_ENVS)
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())

    home = sidekick_home()
    candidates.append((home / "sidekick-agent").resolve())
    candidates.append((home / "hermes-agent").resolve())
    # Monorepo: ourselves (discovery in shared.runtime should prefer the running repo)
    candidates.append((repo_root).resolve())
    candidates.append((repo_root.parent / "sidekick-agent").resolve())
    candidates.append((Path.home() / ".sidekick" / "sidekick-agent").resolve())
    candidates.append((Path.home() / ".hermes" / "hermes-agent").resolve())

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if (candidate / "run_agent.py").exists():
            return candidate
    return None


def discover_python(agent_dir: Path | None, repo_root: Path) -> str:
    explicit = _env_first(*WEB_PYTHON_ENVS)
    if explicit:
        return explicit

    candidates: list[Path] = []
    if agent_dir is not None:
        candidates.extend(
            [
                agent_dir / "venv" / "Scripts" / "python.exe",
                agent_dir / ".venv" / "Scripts" / "python.exe",
                agent_dir / "venv" / "bin" / "python",
                agent_dir / ".venv" / "bin" / "python",
            ]
        )

    candidates.extend(
        [
            repo_root / ".venv" / "Scripts" / "python.exe",
            repo_root / ".venv" / "bin" / "python",
        ]
    )

    for candidate in candidates:
        if candidate.exists():
            return str(candidate.resolve())

    return shutil.which("python") or shutil.which("python3") or "python"


def build_web_runtime(repo_root: Path) -> WebRuntimeConfig:
    agent_dir = discover_agent_dir(repo_root)
    return WebRuntimeConfig(
        host=_env_first(*WEB_HOST_ENVS, default="127.0.0.1"),
        port=int(_env_first(*WEB_PORT_ENVS, default="8787")),
        state_dir=web_state_dir(),
        agent_dir=agent_dir,
        python_exe=discover_python(agent_dir, repo_root),
    )


def build_runtime_report(repo_root: Path) -> dict[str, object]:
    web = build_web_runtime(repo_root)
    return {
        "repo_root": str(repo_root),
        "sidekick_home": str(sidekick_home()),
        "state_dir": str(state_dir()),
        "web": {
            "host": web.host,
            "port": web.port,
            "state_dir": str(web.state_dir),
            "agent_dir": str(web.agent_dir) if web.agent_dir else None,
            "python_exe": web.python_exe,
        },
    }
