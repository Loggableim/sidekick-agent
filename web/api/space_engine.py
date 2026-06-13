"""
Sidekick — Space Engine v2.

Each Space owns everything: sessions, agents, kanban, memory.
Agents are roles INSIDE a space — no more global profiles.

Directory layout::

    HERMES_HOME/spaces/
      nova/                     → fresh-install default Sidekick space
        space.yaml              → model, provider, color, project_dir
        agents/
          default/
            SOUL.md             → system prompt (Persönlichkeit)
            skills.toml         → skill selection
          architect/
            SOUL.md
            skills.toml
        sessions/               → chat logs
        kanban.db               → tasks
        memory/                 → future: vector store per space

Backward-compat: reads old ``workspaces/`` dir as fallback.
"""

from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────────

_SPACES_ROOT_KEY = "HERMES_WEBUI_SPACES_DIR"
_OLD_WORKSPACES_KEY = "HERMES_WEBUI_WORKSPACES_DIR"

SPACES_ROOT: Path = Path(
    os.getenv("SIDEKICK_WEBUI_SPACES_DIR")
    or os.getenv(
        _SPACES_ROOT_KEY,
        str(Path(os.getenv("SIDEKICK_HOME") or os.getenv("HERMES_HOME", str(Path.home() / ".sidekick"))) / "spaces"),
    )
).expanduser().resolve()

# Old workspaces dir for backward compat
_OLD_ROOT: Path = Path(
    os.getenv("SIDEKICK_WEBUI_WORKSPACES_DIR")
    or os.getenv(
        _OLD_WORKSPACES_KEY,
        str(Path(os.getenv("SIDEKICK_HOME") or os.getenv("HERMES_HOME", str(Path.home() / ".sidekick"))) / "workspaces"),
    )
).expanduser().resolve()

_AGENT_SLUG_RE = None  # lazy import

DEFAULT_SPACE_SLUG = (
    (os.getenv("SIDEKICK_WEBUI_DEFAULT_SPACE", "").strip()
     or os.getenv("HERMES_WEBUI_DEFAULT_SPACE", "nova").strip()).lower()
    or "nova"
)
DEFAULT_SPACE_NAME = (
    os.getenv("SIDEKICK_WEBUI_DEFAULT_SPACE_NAME", "").strip()
    or os.getenv("HERMES_WEBUI_DEFAULT_SPACE_NAME", "Nova").strip()
    or "Nova"
)
DEFAULT_SPACE_ALIASES = {
    alias.strip().lower()
    for alias in (
        os.getenv("SIDEKICK_WEBUI_DEFAULT_SPACE_ALIASES", "")
        or os.getenv("HERMES_WEBUI_DEFAULT_SPACE_ALIASES", "novaspace,nova-space,nova_space")
    ).split(",")
    if alias.strip()
}
DEFAULT_SPACE_ALIASES.discard(DEFAULT_SPACE_SLUG)
LEGACY_DEFAULT_SPACE_SLUG = "default"
PROTECTED_SPACE_SLUGS = {DEFAULT_SPACE_SLUG, LEGACY_DEFAULT_SPACE_SLUG}
CONSCIOUSNESS_SOURCE_SPACE_SLUG = (
    os.getenv("SIDEKICK_WEBUI_CONSCIOUSNESS_SPACE", "").strip().lower()
    or os.getenv("HERMES_WEBUI_CONSCIOUSNESS_SPACE", "bewusstsein").strip().lower()
    or "bewusstsein"
)
DEFAULT_NOVA_CHARACTER = (
    os.getenv("SIDEKICK_WEBUI_DEFAULT_NOVA_CHARACTER", "").strip()
    or os.getenv("HERMES_WEBUI_DEFAULT_NOVA_CHARACTER", "nova").strip()
    or "nova"
)
_GENERIC_DEFAULT_SOUL_MARKER = "Customize this SOUL.md to define your personality and behavior."


def _normalize_space_slug(slug: str) -> str:
    value = str(slug or "").strip().lower()
    if value in DEFAULT_SPACE_ALIASES:
        return DEFAULT_SPACE_SLUG
    return value


def _is_empty_configless_space_dir(path: Path) -> bool:
    """Return True for migration artefacts such as an empty ``novaspace`` dir."""
    if not path.is_dir():
        return False
    if (path / "space.yaml").exists() or (path / "workspace.yaml").exists():
        return False
    sessions_dir = path / "sessions"
    if sessions_dir.is_dir() and any(sessions_dir.glob("*.json")):
        return False
    allowed_dirs = {"agents", "memory", "sessions"}
    allowed_files = {"nul"}
    for child in path.iterdir():
        if child.is_dir() and child.name in allowed_dirs:
            continue
        if child.is_file() and child.name.lower() in allowed_files:
            continue
        return False
    return True

# ── Exceptions ──────────────────────────────────────────────────────────────

class SpaceError(Exception):
    """Base exception for space operations."""


class SpaceNotFound(SpaceError):
    """Requested space does not exist."""


class SpaceExists(SpaceError):
    """Space slug already taken."""


class AgentNotFound(SpaceError):
    """Agent does not exist in this space."""


# ═══════════════════════════════════════════════════════════════════════════════
# Space Model
# ═══════════════════════════════════════════════════════════════════════════════

class Space:
    """A single Space with its own agents, sessions, kanban, memory."""

    def __init__(self, slug: str, name: str = "", *, custom_root: Path | None = None) -> None:
        self.slug = slug.strip().lower()
        self.name = name or slug
        self._custom_root = custom_root

    # ── Paths ────────────────────────────────────────────────────────────────

    @property
    def root(self) -> Path:
        return (self._custom_root or SPACES_ROOT) / self.slug

    @property
    def config_path(self) -> Path:
        return self.root / "space.yaml"

    @property
    def agents_dir(self) -> Path:
        return self.root / "agents"

    @property
    def sessions_dir(self) -> Path:
        return self.root / "sessions"

    @property
    def kanban_path(self) -> Path:
        return self.root / "kanban.db"

    @property
    def memory_dir(self) -> Path:
        configured = str(self.load_config().get("memory_path") or "").strip()
        if not configured:
            return self.root / "memory"
        raw = Path(configured).expanduser()
        if raw.is_absolute():
            return raw.resolve()
        resolved = (self.root / raw).resolve()
        root = self.root.resolve()
        if not resolved.is_relative_to(root):
            raise ValueError(f"memory_path for space {self.slug!r} must stay inside the space directory")
        return resolved

    # ── Config ──────────────────────────────────────────────────────────────

    CONFIG_DEFAULTS = {
        "name": "",
        "model": {"default": "", "provider": ""},
        "reasoning_effort": "",
        "personality": "",
        "description": "",
        "project_dir": "",
        "memory_path": "",
        "color": "#4FC3F7",
        "emoji": "📁",
        "nova": {
            "enabled": False,
            "character": "",
            "source_space": CONSCIOUSNESS_SOURCE_SPACE_SLUG,
            "communication_mode": "pingpong",
        },
    }

    def load_config(self) -> dict:
        """Load space.yaml with sensible defaults for missing fields."""
        if not self.config_path.exists():
            return dict(self.CONFIG_DEFAULTS)
        try:
            import yaml
            raw = yaml.safe_load(self.config_path.read_text("utf-8")) or {}
        except Exception:
            logger.exception("failed to parse %s, using defaults", self.config_path)
            return dict(self.CONFIG_DEFAULTS)

        result = dict(self.CONFIG_DEFAULTS)
        if isinstance(raw.get("model"), dict):
            result["model"].update(raw["model"])
        for key in self.CONFIG_DEFAULTS:
            if key == "model":
                continue
            if key == "nova":
                if isinstance(raw.get("nova"), dict):
                    result["nova"].update(raw["nova"])
                continue
            if key in raw:
                result[key] = raw[key]
        # Pass through app configs (per-space accounts/bots)
        if "gmail" in raw:
            result["gmail"] = raw["gmail"]
        if "discord" in raw:
            result["discord"] = raw["discord"]
        return result

    def save_config(self, config: dict) -> None:
        """Persist space.yaml (only known fields)."""
        import yaml
        self.root.mkdir(parents=True, exist_ok=True)
        out: dict = {}
        # Save name if present and differs from slug
        if "name" in config:
            out["name"] = config["name"]
        if "model" in config:
            out["model"] = config["model"]
        for key in self.CONFIG_DEFAULTS:
            if key in ("name", "model"):
                continue
            if key == "nova":
                nova_cfg = config.get("nova")
                if isinstance(nova_cfg, dict):
                    out["nova"] = dict(self.CONFIG_DEFAULTS["nova"])
                    out["nova"].update(nova_cfg)
                continue
            if key in config:
                out[key] = config[key]
        # App configs saved as top-level keys
        if "gmail" in config:
            out["gmail"] = config["gmail"]
        if "discord" in config:
            out["discord"] = config["discord"]
        self.config_path.write_text(yaml.dump(out, default_flow_style=False), "utf-8")

    def get_project_dir(self) -> str | None:
        """Return project_dir from config, or None if not set/invalid."""
        pdir = self.load_config().get("project_dir", "").strip()
        if not pdir:
            # Legacy compatibility: the old "default" space historically pointed
            # at the software root. The fresh-install default is now "nova" and
            # intentionally stays naked unless the user sets a project_dir.
            if self.slug == LEGACY_DEFAULT_SPACE_SLUG:
                from web.api.config import REPO_ROOT
                return str(REPO_ROOT)
            return None
        p = Path(pdir).expanduser().resolve()
        if p.is_dir():
            return str(p)
        logger.warning("project_dir %r for space %r does not exist", pdir, self.slug)
        return None

    # ── Agent Discovery ─────────────────────────────────────────────────────

    def list_agents(self) -> list[str]:
        """Discover agent slugs in this space's agents/ directory."""
        if not self.agents_dir.is_dir():
            return []
        return sorted(
            d.name for d in self.agents_dir.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        )

    def agent_path(self, agent_slug: str) -> Path:
        """Return the directory for an agent. Does NOT check existence."""
        slug = agent_slug.strip().lower()
        return self.agents_dir / slug

    def agent_soul_path(self, agent_slug: str) -> Path:
        return self.agent_path(agent_slug) / "SOUL.md"

    def agent_skills_path(self, agent_slug: str) -> Path:
        return self.agent_path(agent_slug) / "skills.toml"

    def agent_model_override_path(self, agent_slug: str) -> Path:
        return self.agent_path(agent_slug) / "model-override.yaml"

    def get_agent_soul(self, agent_slug: str) -> str:
        """Read an agent's SOUL.md, return empty string if missing."""
        p = self.agent_soul_path(agent_slug)
        if p.exists():
            return p.read_text("utf-8")
        return ""

    def get_agent_skills(self, agent_slug: str) -> list[str]:
        """Read agent's skills.toml → list of skill names. Empty list = all."""
        p = self.agent_skills_path(agent_slug)
        if not p.exists():
            return []  # empty = inherit global skills
        try:
            import tomllib
            data = tomllib.loads(p.read_text("utf-8"))
            return data.get("skills", []) or []
        except Exception:
            logger.exception("failed to parse %s", p)
            return []

    def ensure_agent(self, agent_slug: str, *, create_soul: bool = True) -> Path:
        """Create agent directory + default SOUL.md if missing. Returns path."""
        ap = self.agent_path(agent_slug)
        ap.mkdir(parents=True, exist_ok=True)
        soul = ap / "SOUL.md"
        if create_soul and not soul.exists():
            soul.write_text(
                f"# {agent_slug} — Agent in space {self.slug}\n\n"
                f"You are the **{agent_slug}** agent working in the **{self.name or self.slug}** space.\n"
                f"Customize this SOUL.md to define your personality and behavior.\n",
                "utf-8",
            )
        return ap

    # ── Serialization ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        cfg = self.load_config()
        return {
            "slug": self.slug,
            "name": cfg.get("name") or self.name or self.slug,
            "description": cfg.get("description", ""),
            "model": cfg.get("model", {}),
            "reasoning_effort": cfg.get("reasoning_effort", ""),
            "personality": cfg.get("personality", ""),
            "project_dir": cfg.get("project_dir", ""),
            "color": cfg.get("color", "#4FC3F7"),
            "emoji": cfg.get("emoji", "📁"),
            "agents": self.list_agents(),
            "session_count": self._session_count(),
        }

    def _session_count(self) -> int:
        if not self.sessions_dir.exists():
            return 0
        return len(list(self.sessions_dir.glob("*.json")))

    def __repr__(self) -> str:
        return f"<Space slug={self.slug!r} name={self.name!r}>"


# ═══════════════════════════════════════════════════════════════════════════════
# Registry (cached)
# ═══════════════════════════════════════════════════════════════════════════════

_SPACE_CACHE: list[Space] | None = None
_SPACE_CACHE_TS: float = 0.0
_CACHE_TTL: float = 5.0


def _invalidate_space_cache() -> None:
    global _SPACE_CACHE, _SPACE_CACHE_TS
    _SPACE_CACHE = None
    _SPACE_CACHE_TS = 0.0


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text("utf-8")
    except Exception:
        return ""


def _is_generic_default_agent_soul(path: Path) -> bool:
    text = _safe_read_text(path)
    return bool(text and _GENERIC_DEFAULT_SOUL_MARKER in text)


def _resolve_consciousness_source_slug() -> str:
    canonical_default = Space(DEFAULT_SPACE_SLUG, DEFAULT_SPACE_NAME)
    if (canonical_default.root / "SOUL.md").exists():
        return DEFAULT_SPACE_SLUG
    return CONSCIOUSNESS_SOURCE_SPACE_SLUG


def _uses_legacy_consciousness_alias() -> bool:
    if CONSCIOUSNESS_SOURCE_SPACE_SLUG == DEFAULT_SPACE_SLUG:
        return False
    canonical_default = Space(DEFAULT_SPACE_SLUG, DEFAULT_SPACE_NAME)
    legacy_space = Space(CONSCIOUSNESS_SOURCE_SPACE_SLUG, CONSCIOUSNESS_SOURCE_SPACE_SLUG)
    return canonical_default.root.is_dir() and legacy_space.root.is_dir()


def _seed_default_space_from_consciousness() -> None:
    """Bootstrap the canonical Nova space from the consciousness source.

    This is intentionally conservative:
    - never overwrites an existing non-generic target file
    - only copies a minimal identity/config set
    - keeps the source space untouched
    """
    source_slug = _resolve_consciousness_source_slug()
    if source_slug == DEFAULT_SPACE_SLUG:
        target = Space(DEFAULT_SPACE_SLUG, DEFAULT_SPACE_NAME)
        if target.root.is_dir():
            cfg = target.load_config()
            changed = False
            nova_cfg = dict(cfg.get("nova") or {})
            desired_nova = {
                "enabled": True,
                "character": nova_cfg.get("character", "") or DEFAULT_NOVA_CHARACTER,
                "source_space": DEFAULT_SPACE_SLUG,
                "communication_mode": "pingpong",
            }
            if nova_cfg != desired_nova:
                cfg["nova"] = desired_nova
                changed = True
            if not cfg.get("name"):
                cfg["name"] = DEFAULT_SPACE_NAME
                changed = True
            if changed:
                target.save_config(cfg)
        return

    source = Space(source_slug, source_slug)
    if not source.root.is_dir():
        return

    target = Space(DEFAULT_SPACE_SLUG, DEFAULT_SPACE_NAME)
    target.root.mkdir(parents=True, exist_ok=True)
    target.memory_dir.mkdir(parents=True, exist_ok=True)
    target.ensure_agent("default", create_soul=True)

    source_config = source.config_path
    target_config = target.config_path
    if source_config.exists() and not target_config.exists():
        shutil.copy2(source_config, target_config)

    source_root_soul = source.root / "SOUL.md"
    target_root_soul = target.root / "SOUL.md"
    if source_root_soul.exists() and not target_root_soul.exists():
        shutil.copy2(source_root_soul, target_root_soul)

    source_agent_soul = source_root_soul
    target_agent_soul = target.agent_soul_path("default")
    if source_agent_soul.exists() and (
        not target_agent_soul.exists() or _is_generic_default_agent_soul(target_agent_soul)
    ):
        shutil.copy2(source_agent_soul, target_agent_soul)

    cfg = target.load_config()
    changed = False
    if not cfg.get("name"):
        cfg["name"] = DEFAULT_SPACE_NAME
        changed = True
    if not cfg.get("description"):
        cfg["description"] = (
            f"Canonical Nova space bootstrapped from {source_slug}."
        )
        changed = True
    nova_cfg = dict(cfg.get("nova") or {})
    desired_nova = {
        "enabled": True,
        "character": nova_cfg.get("character", "") or DEFAULT_NOVA_CHARACTER,
        "source_space": DEFAULT_SPACE_SLUG,
        "communication_mode": "pingpong",
    }
    if nova_cfg != desired_nova:
        cfg["nova"] = desired_nova
        changed = True
    if changed:
        target.save_config(cfg)


def seed_space_with_nova(
    space: Space,
    *,
    character: str = "",
    source_slug: str | None = None,
) -> None:
    """Attach a Nova instance to an arbitrary space without clobbering custom files."""
    source_slug = source_slug or _resolve_consciousness_source_slug()
    source = Space(source_slug, source_slug)
    if not source.root.is_dir():
        return

    space.root.mkdir(parents=True, exist_ok=True)
    space.memory_dir.mkdir(parents=True, exist_ok=True)
    space.ensure_agent("default", create_soul=True)

    source_root_soul = source.root / "SOUL.md"
    target_root_soul = space.root / "SOUL.md"
    if source_root_soul.exists() and not target_root_soul.exists():
        shutil.copy2(source_root_soul, target_root_soul)

    target_agent_soul = space.agent_soul_path("default")
    if source_root_soul.exists() and (
        not target_agent_soul.exists() or _is_generic_default_agent_soul(target_agent_soul)
    ):
        shutil.copy2(source_root_soul, target_agent_soul)

    current = space.load_config()
    nova_cfg = dict(current.get("nova") or {})
    nova_cfg.update(
        {
            "enabled": True,
            "character": character or nova_cfg.get("character", ""),
            "source_space": source_slug,
            "communication_mode": "pingpong",
        }
    )
    current["nova"] = nova_cfg
    if not current.get("description"):
        current["description"] = f"Space with its own Nova instance seeded from {source_slug}."
    space.save_config(current)


def _scan_fs_for_spaces() -> list[Space]:
    """Scan SPACES_ROOT for space directories + fallback to OLD_ROOT."""
    _seed_default_space_from_consciousness()
    seen_slugs: set[str] = set()
    spaces: list[Space] = []

    # Primary: new spaces/ dir
    roots_to_scan = [(SPACES_ROOT, False)]
    # Backward compat: old workspaces/ dir (never-create)
    if _OLD_ROOT != SPACES_ROOT and _OLD_ROOT.is_dir():
        roots_to_scan.append((_OLD_ROOT, True))

    for root, is_legacy in roots_to_scan:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not child.is_dir():
                continue
            slug = child.name.strip().lower()
            if slug in DEFAULT_SPACE_ALIASES and _is_empty_configless_space_dir(child):
                continue
            slug = _normalize_space_slug(slug)
            if slug in seen_slugs:
                continue  # new-format wins over old-format
            if slug == CONSCIOUSNESS_SOURCE_SPACE_SLUG and _uses_legacy_consciousness_alias():
                continue
            seen_slugs.add(slug)
            space = Space(slug, slug, custom_root=root if is_legacy else None)
            # If this is an old-format dir with workspace.yaml, migrate on read
            old_config = child / "workspace.yaml"
            if is_legacy and old_config.exists() and not space.config_path.exists():
                _soft_migrate_workspace(space, old_config)
            spaces.append(space)

    # Fresh install: create a naked Nova space. Existing installs keep their
    # previously scanned spaces (including legacy "default" or user-renamed ones)
    # and are not force-migrated.
    if not spaces:
        space = Space(DEFAULT_SPACE_SLUG, DEFAULT_SPACE_NAME)
        space.root.mkdir(parents=True, exist_ok=True)
        space.memory_dir.mkdir(parents=True, exist_ok=True)
        space.ensure_agent("default", create_soul=True)
        cfg = space.load_config()
        cfg.update({
            "name": DEFAULT_SPACE_NAME,
            "description": "Fresh Sidekick space for Nova. Add a project directory when you want to attach files.",
            "emoji": "✨",
            "color": "#7c5cfc",
        })
        space.save_config(cfg)
        spaces.append(space)

    return spaces


def _soft_migrate_workspace(space: Space, old_yaml: Path) -> None:
    """Copy workspace.yaml → space.yaml on first read (does not delete old)."""
    try:
        import yaml
        data = yaml.safe_load(old_yaml.read_text("utf-8")) or {}
        space.root.mkdir(parents=True, exist_ok=True)
        space.save_config(data)
        logger.info("migrated workspace.yaml → space.yaml for %s", space.slug)
    except Exception:
        logger.debug("soft-migrate failed for %s", space.slug)


# ── Public Registry API ─────────────────────────────────────────────────────

def get_all_spaces() -> list[Space]:
    """List all spaces (cached)."""
    global _SPACE_CACHE, _SPACE_CACHE_TS
    now = time.time()
    if _SPACE_CACHE is not None and (now - _SPACE_CACHE_TS) < _CACHE_TTL:
        return _SPACE_CACHE
    _SPACE_CACHE = _scan_fs_for_spaces()
    _SPACE_CACHE_TS = now
    return _SPACE_CACHE


def get_space(slug: str) -> Space | None:
    """Look up a space by slug."""
    slug = _normalize_space_slug(slug)
    if slug == CONSCIOUSNESS_SOURCE_SPACE_SLUG and _uses_legacy_consciousness_alias():
        slug = DEFAULT_SPACE_SLUG
    for s in get_all_spaces():
        if s.slug == slug:
            return s
    return None


def get_or_create_space(slug: str, name: str = "") -> Space:
    """Return existing space or create a new one with default agent."""
    slug = _normalize_space_slug(slug)
    existing = get_space(slug)
    if existing:
        existing.memory_dir.mkdir(parents=True, exist_ok=True)
        if existing.slug == DEFAULT_SPACE_SLUG:
            _seed_default_space_from_consciousness()
        return existing
    space = Space(slug, name or slug)
    space.root.mkdir(parents=True, exist_ok=True)
    space.memory_dir.mkdir(parents=True, exist_ok=True)
    space.ensure_agent("default", create_soul=True)
    cfg = space.load_config()
    cfg["name"] = name or (DEFAULT_SPACE_NAME if space.slug == DEFAULT_SPACE_SLUG else space.slug)
    space.save_config(cfg)
    if space.slug == DEFAULT_SPACE_SLUG:
        _seed_default_space_from_consciousness()
    _invalidate_space_cache()
    return space


def create_space(
    slug: str,
    name: str = "",
    color: str = "",
    *,
    nova_instance: bool = False,
    nova_character: str = "",
) -> Space:
    """Create a brand-new space. Raises SpaceExists if slug taken."""
    slug = _normalize_space_slug(slug)
    if get_space(slug):
        raise SpaceExists(f"space {slug!r} already exists")
    space = Space(slug, name or slug)
    space.root.mkdir(parents=True, exist_ok=True)
    space.memory_dir.mkdir(parents=True, exist_ok=True)
    space.ensure_agent("default", create_soul=True)
    cfg = space.load_config()
    cfg["name"] = name or (DEFAULT_SPACE_NAME if space.slug == DEFAULT_SPACE_SLUG else space.slug)
    if color:
        cfg["color"] = color
    space.save_config(cfg)
    if space.slug == DEFAULT_SPACE_SLUG:
        _seed_default_space_from_consciousness()
    elif nova_instance:
        seed_space_with_nova(space, character=nova_character)
    _invalidate_space_cache()
    return space


def delete_space(slug: str) -> bool:
    """Remove a space entirely. The fresh default and legacy default are protected."""
    slug = slug.strip().lower()
    if slug in PROTECTED_SPACE_SLUGS:
        logger.warning("refusing to delete protected default space %s", slug)
        return False
    candidates = []
    for root in (SPACES_ROOT, _OLD_ROOT):
        path = root / slug
        if path.is_dir() and path not in candidates:
            candidates.append(path)
    if not candidates:
        return False
    import shutil
    for path in candidates:
        try:
            shutil.rmtree(path)
        except Exception:
            logger.exception("failed to delete space directory %s", path)
            _invalidate_space_cache()
            return False
    _invalidate_space_cache()
    remaining = [path for path in candidates if path.exists()]
    if remaining:
        logger.error("space delete reported incomplete for %s: %s", slug, remaining)
        return False
    return get_space(slug) is None


# ═══════════════════════════════════════════════════════════════════════════════
# Agent CRUD within a Space
# ═══════════════════════════════════════════════════════════════════════════════

def list_space_agents(slug: str) -> list[str]:
    """List agent slugs in a space."""
    space = get_space(slug)
    if not space:
        raise SpaceNotFound(f"space {slug!r} not found")
    return space.list_agents()


def ensure_space_agent(slug: str, agent_slug: str) -> Path:
    """Ensure an agent directory exists within a space."""
    space = get_or_create_space(slug)
    return space.ensure_agent(agent_slug)


def delete_space_agent(slug: str, agent_slug: str) -> bool:
    """Delete an agent from a space. 'default' agent is protected."""
    if agent_slug.strip().lower() == "default":
        return False
    space = get_space(slug)
    if not space:
        return False
    ap = space.agent_path(agent_slug)
    if not ap.is_dir():
        return False
    import shutil
    shutil.rmtree(ap, ignore_errors=True)
    return True


# ═══════════════════════════════════════════════════════════════════════════════
# Thread-local active space (for request context)
# ═══════════════════════════════════════════════════════════════════════════════

_ACTIVE_SPACE_LOCAL = threading.local()


def set_active_space(slug_or_none: str | None) -> None:
    _ACTIVE_SPACE_LOCAL.slug = slug_or_none.strip().lower() if slug_or_none else None


def clear_active_space() -> None:
    try:
        del _ACTIVE_SPACE_LOCAL.slug
    except AttributeError:
        pass


def get_active_space_slug() -> str | None:
    return getattr(_ACTIVE_SPACE_LOCAL, "slug", None)


def resolve_active_space() -> Space:
    """Resolve the active space (never returns None)."""
    slug = get_active_space_slug()
    if not slug:
        slug = (os.getenv("SIDEKICK_WEBUI_ACTIVE_WORKSPACE") or os.getenv("HERMES_WEBUI_ACTIVE_WORKSPACE", "")).strip().lower() or DEFAULT_SPACE_SLUG
    return get_or_create_space(slug)


# ── Backward compat aliases ────────────────────────────────────────────────
# Keep the old names working so existing imports (streaming.py, routes.py)
# don't break during migration. These will be removed in a future cleanup.

def get_all_workspaces() -> list:
    """Alias — delegates to get_all_spaces()."""
    return get_all_spaces()


def get_workspace(slug: str):
    """Alias — delegates to get_space()."""
    return get_space(slug)


def get_or_create_workspace(slug: str, name: str = ""):
    """Alias — delegates to get_or_create_space()."""
    return get_or_create_space(slug, name)


def create_workspace(slug: str, name: str = "", color: str = "", **extra):
    """Alias — delegates to create_space(). Forward extra kwargs (nova_instance, nova_character)."""
    return create_space(slug, name, color, **extra)


def delete_workspace(slug: str) -> bool:
    """Alias — delegates to delete_space()."""
    return delete_space(slug)


def set_active_workspace(slug_or_none: str | None) -> None:
    """Alias — delegates to set_active_space() + workspace_isolation()."""
    set_active_space(slug_or_none)
    try:
        from web.api.workspace_isolation import set_active_workspace as _ws_set_active
        _ws_set_active(slug_or_none)
    except Exception:
        pass


def clear_active_workspace() -> None:
    """Alias — delegates to clear_active_space() + workspace_isolation()."""
    clear_active_space()
    try:
        from web.api.workspace_isolation import clear_active_workspace as _ws_clear_active
        _ws_clear_active()
    except Exception:
        pass


def get_active_workspace_slug() -> str | None:
    """Alias — delegates to get_active_space_slug()."""
    return get_active_space_slug()


def resolve_active_workspace():
    """Alias — delegates to resolve_active_space()."""
    return resolve_active_space()
