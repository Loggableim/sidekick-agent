"""
Sidekick -- Workspace Isolation System (Spaces).

Each Space (= workspace) has its own:
  - ``sessions/`` directory   → isolated chat logs
  - ``kanban.db``             → isolated kanban board
  - ``workspace.yaml``        → model, provider, reasoning_effort, personality
  - ``SOUL.md``               → per-space system prompt (optional)

Directory layout::

    HERMES_HOME/workspaces/
      nova/                     → fresh-install default Sidekick space
        workspace.yaml
        sessions/
        kanban.db
      projekt-x/
        ...
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Root directory for all workspaces ─────────────────────────────────────────
DEFAULT_WORKSPACE_SLUG = (os.getenv("SIDEKICK_WEBUI_DEFAULT_SPACE") or "").strip().lower() or "nova"

WORKSPACES_ROOT = (
    Path(
        os.getenv("SIDEKICK_WEBUI_WORKSPACES_DIR")
        or os.getenv(
            "HERMES_WEBUI_WORKSPACES_DIR",
            str(Path(os.getenv("SIDEKICK_HOME") or os.getenv("SIDEKICK_HOME", str(Path.home() / ".sidekick"))) / "workspaces"),
        )
    )
    .expanduser()
    .resolve()
)


# ── Workspace class ─────────────────────────────────────────────────────────────

class Workspace:
    """A single workspace (space) with its own state sub-directories."""

    def __init__(self, slug: str, name: str = "") -> None:
        self.slug = slug
        self.name = name or slug

    # ── Paths ────────────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return WORKSPACES_ROOT / self.slug

    @property
    def config_path(self) -> Path:
        return self.root / "workspace.yaml"

    @property
    def soul_path(self) -> Path:
        return self.root / "SOUL.md"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def kanban_path(self) -> Path:
        return self.root / "kanban.db"

    # ── Config ───────────────────────────────────────────────────────────────

    def load_config(self) -> dict:
        """Load workspace config (model, provider, personality, project_dir, ...).

        Returns sensible defaults if the file doesn't exist yet.
        """
        defaults = {
            "model": {"default": "", "provider": ""},
            "reasoning_effort": "",
            "personality": "",
            "description": "",
            "project_dir": "",
            "color": "#4FC3F7",
        }
        if not self.config_path.exists():
            return dict(defaults)
        try:
            import yaml
            raw = yaml.safe_load(self.config_path.read_text("utf-8")) or {}
        except Exception:
            logger.exception("failed to parse %s, using defaults", self.config_path)
            return dict(defaults)

        result = dict(defaults)
        if isinstance(raw.get("model"), dict):
            result["model"].update(raw["model"])
        for key in ("reasoning_effort", "personality", "description", "project_dir", "color"):
            if key in raw:
                result[key] = raw[key]
        # Pass through gmail config (per-space accounts)
        if "gmail" in raw:
            result["gmail"] = raw["gmail"]
        return result

    def save_config(self, config: dict) -> None:
        """Persist workspace config (only the workspace-scoped fields)."""
        import yaml
        self.root.mkdir(parents=True, exist_ok=True)
        out: dict = {}
        if "model" in config:
            out["model"] = config["model"]
        for key in ("reasoning_effort", "personality", "description", "project_dir", "color"):
            if key in config:
                out[key] = config[key]
        # Gmail config saved as top-level key — survives through full round-trip
        if "gmail" in config:
            out["gmail"] = config["gmail"]
        self.config_path.write_text(yaml.dump(out, default_flow_style=False), "utf-8")

    # ── Serialization ────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        cfg = self.load_config()
        return {
            "slug": self.slug,
            "name": self.name,
            "description": cfg.get("description", ""),
            "model": cfg.get("model", {}),
            "reasoning_effort": cfg.get("reasoning_effort", ""),
            "personality": cfg.get("personality", ""),
            "project_dir": cfg.get("project_dir", ""),
            "color": cfg.get("color", "#4FC3F7"),
            "soul_exists": self.soul_path.exists(),
            "session_count": self._session_count(),
        }

    def get_project_dir(self) -> str | None:
        """Return the project_dir from config, or None if not set/invalid."""
        pdir = self.load_config().get("project_dir", "").strip()
        if not pdir:
            # Legacy compatibility: the old "default" workspace historically
            # pointed at the software root. The fresh-install default is now
            # "nova" and intentionally stays naked unless project_dir is set.
            if self.slug == "default":
                from web.api.config import REPO_ROOT
                return str(REPO_ROOT)
            return None
        p = Path(pdir).expanduser().resolve()
        if p.is_dir():
            return str(p)
        logger.warning("project_dir %r for workspace %r does not exist", pdir, self.slug)
        return None

    def _session_count(self) -> int:
        if not self.sessions_dir.exists():
            return 0
        return len(list(self.sessions_dir.glob("*.json")))

    def __repr__(self) -> str:
        return f"<Workspace slug={self.slug!r} name={self.name!r}>"


# ── Registry ───────────────────────────────────────────────────────────────────

_WORKSPACE_CACHE: list[Workspace] | None = None
_WORKSPACE_CACHE_TS: float = 0.0
_CACHE_TTL: float = 5.0


def _invalidate_cache() -> None:
    global _WORKSPACE_CACHE, _WORKSPACE_CACHE_TS
    _WORKSPACE_CACHE = None
    _WORKSPACE_CACHE_TS = 0.0


def get_workspace(slug: str) -> Workspace | None:
    """Look up a workspace by slug. Returns ``None`` if not found."""
    slug = slug.strip().lower()
    for ws in get_all_workspaces():
        if ws.slug == slug:
            return ws
    return None


def get_all_workspaces() -> list[Workspace]:
    """List all workspaces, sorted by modification time (newest first).

    Results are cached for ``_CACHE_TTL`` seconds.
    """
    global _WORKSPACE_CACHE, _WORKSPACE_CACHE_TS
    now = time.time()
    if _WORKSPACE_CACHE is not None and (now - _WORKSPACE_CACHE_TS) < _CACHE_TTL:
        return _WORKSPACE_CACHE

    workspaces: list[Workspace] = []
    if WORKSPACES_ROOT.is_dir():
        for child in sorted(WORKSPACES_ROOT.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if child.is_dir():
                slug = child.name
                name = slug  # default: slug == name
                # Try to read a human-friendly name from workspace.yaml
                ws = Workspace(slug, name)
                workspaces.append(ws)

    # Fresh install: create the naked Nova workspace instead of exposing users
    # to the legacy "default" name.
    if not workspaces:
        ws = Workspace(DEFAULT_WORKSPACE_SLUG, DEFAULT_WORKSPACE_SLUG)
        ws.root.mkdir(parents=True, exist_ok=True)
        workspaces.append(ws)

    _WORKSPACE_CACHE = workspaces
    _WORKSPACE_CACHE_TS = now
    return workspaces


def get_or_create_workspace(slug: str, name: str = "") -> Workspace:
    """Return an existing workspace or create a new one."""
    existing = get_workspace(slug)
    if existing:
        return existing
    ws = Workspace(slug, name or slug)
    ws.root.mkdir(parents=True, exist_ok=True)
    _invalidate_cache()
    return ws


def create_workspace(slug: str, name: str = "", color: str = "") -> Workspace:
    """Create a brand-new workspace. Fails if it already exists."""
    if get_workspace(slug):
        raise ValueError(f"workspace {slug!r} already exists")
    ws = Workspace(slug, name or slug)
    ws.root.mkdir(parents=True, exist_ok=True)
    if color:
        cfg = ws.load_config()
        cfg["color"] = color
        ws.save_config(cfg)
    _invalidate_cache()
    return ws


def delete_workspace(slug: str) -> bool:
    """Remove a workspace directory entirely.

    The fresh default (nova) and legacy default are protected. Returns ``True``
    if deleted, ``False`` if not found or protected.
    """
    if slug.strip().lower() in {DEFAULT_WORKSPACE_SLUG, "default"}:
        logger.warning("refusing to delete protected default workspace %s", slug)
        return False
    ws = get_workspace(slug)
    if not ws or not ws.root.is_dir():
        return False
    import shutil
    shutil.rmtree(ws.root, ignore_errors=True)
    _invalidate_cache()
    return True


# ── Thread-local active workspace for request context ──────────────────────────

_ACTIVE_WS_LOCAL = threading.local()


def set_active_workspace(slug_or_none: str | None) -> None:
    """Set the active workspace slug for the current thread.

    Called once per HTTP request at the start of the handler chain.
    The value is consumed by ``resolve_active_workspace()`` and the
    session/kanban isolation helpers in config.py and kanban_bridge.py.
    """
    _ACTIVE_WS_LOCAL.slug = slug_or_none.strip().lower() if slug_or_none else None


def clear_active_workspace() -> None:
    """Remove the per-thread active workspace."""
    try:
        del _ACTIVE_WS_LOCAL.slug
    except AttributeError:
        pass


def get_active_workspace_slug() -> str | None:
    """Return the active workspace slug for this thread, or ``None``."""
    return getattr(_ACTIVE_WS_LOCAL, 'slug', None)


def resolve_active_workspace() -> Workspace:
    """Resolve the active workspace for the current thread.

    Fallback chain:
      1. ``set_active_workspace()`` value
      2. ``HERMES_WEBUI_ACTIVE_WORKSPACE`` env var
      3. ``nova`` (or ``HERMES_WEBUI_DEFAULT_SPACE``)

    Never returns ``None``.
    """
    slug = get_active_workspace_slug()
    if not slug:
        slug = (os.getenv("SIDEKICK_WEBUI_ACTIVE_WORKSPACE") or "").strip().lower() or DEFAULT_WORKSPACE_SLUG
    ws = get_or_create_workspace(slug)
    return ws
