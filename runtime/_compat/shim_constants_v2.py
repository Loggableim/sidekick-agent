"""Shared constants for Sidekick Agent.

Import-safe module with no dependencies — can be imported from anywhere
without risk of circular imports.

Fallback chain for home directory detection:
  1. SIDEKICK_HOME env var
  2. HERMES_HOME env var (backward compat)
  3. ~/.sidekick/ (preferred)
  4. ~/.sidekick/ (legacy fallback)

All original ``hermes_constants`` names are re-exported so that
``from sidekick_constants import get_sidekick_home`` still works during
migration.
"""

import os
from pathlib import Path

# ─── Re-export everything from hermes_constants for backward compat ────────────
# This ensures that ``from sidekick_constants import get_sidekick_home`` or
# ``from sidekick_constants import OPENROUTER_BASE_URL`` works seamlessly.

from hermes_constants import (  # noqa: F401  # noqa: F401
    OPENROUTER_BASE_URL,
    OPENROUTER_MODELS_URL,
    AI_GATEWAY_BASE_URL,
    VALID_REASONING_EFFORTS,
    get_default_hermes_root,
    get_hermes_dir,
    get_sidekick_home as _get_sidekick_home_original,
    get_optional_skills_dir as _get_optional_skills_dir_original,
    get_subprocess_home,
    is_termux,
    is_wsl,
    is_container,
    parse_reasoning_effort,
    apply_ipv4_preference,
    display_sidekick_home as _display_sidekick_home_original,
    get_config_path as _get_config_path_original,
    get_skills_dir as _get_skills_dir_original,
    get_env_path as _get_env_path_original,
)

# ─── Sidekick home resolution ─────────────────────────────────────────────────

_profile_fallback_warned: bool = False


def get_sidekick_home() -> Path:
    """Return the Sidekick home directory with fallback chain.

    Priority:
      1. **SIDEKICK_HOME** env var (new standard)
      2. **HERMES_HOME** env var (legacy compat)
      3. **~/.sidekick/** (preferred default)
      4. **~/.sidekick/** (legacy fallback — exists on disk from before migration)

    When a non-default profile is sticky-active but neither env var is set,
    emits a one-shot warning via stderr so cross-profile data corruption is
    diagnosable (same behaviour as ``get_sidekick_home()``).
    """
    val = os.environ.get("SIDEKICK_HOME", "").strip()
    if val:
        return Path(val)

    # Legacy fallback: HERMES_HOME still set
    val = os.environ.get("HERMES_HOME", "").strip()
    if val:
        return Path(val)

    # Default: prefer ~/.sidekick/ but accept ~/.sidekick/ if it exists
    sidekick_default = Path.home() / ".sidekick"
    hermes_legacy = Path.home() / ".hermes"

    # Profile-warning guard (mirrors get_sidekick_home behaviour)
    global _profile_fallback_warned
    if not _profile_fallback_warned:
        try:
            active_path = hermes_legacy / "active_profile"
            active = active_path.read_text().strip() if active_path.exists() else ""
        except (UnicodeDecodeError, OSError):
            active = ""
        if active and active != "default":
            _profile_fallback_warned = True
            import sys
            msg = (
                f"[SIDEKICK_HOME fallback] SIDEKICK_HOME is unset but active "
                f"profile is {active!r}. Falling back to default home, which "
                f"is the DEFAULT profile — not {active!r}. Any data this "
                f"process writes will land in the wrong profile. "
                f"The subprocess spawner should pass SIDEKICK_HOME explicitly."
            )
            try:
                sys.stderr.write(msg + "\n")
                sys.stderr.flush()
            except Exception:
                pass

    if sidekick_default.exists():
        return sidekick_default

    return hermes_legacy


def get_default_sidekick_root() -> Path:
    """Return the root Sidekick directory for profile-level operations.

    Fallback chain mirrors ``get_default_hermes_root()`` but uses
    ``SIDEKICK_HOME`` as primary, ``HERMES_HOME`` as secondary.
    """
    native_sidekick = Path.home() / ".sidekick"
    native_hermes = Path.home() / ".hermes"

    env_home = os.environ.get("SIDEKICK_HOME", "") or os.environ.get("HERMES_HOME", "")
    if not env_home:
        # No env var set — use what get_sidekick_home() would return
        if native_sidekick.exists():
            return native_sidekick
        return native_hermes

    env_path = Path(env_home)
    # Try to resolve against native sidekick home
    try:
        env_path.resolve().relative_to(native_sidekick.resolve())
        return native_sidekick
    except ValueError:
        pass

    # Try against native hermes home
    try:
        env_path.resolve().relative_to(native_hermes.resolve())
        return native_hermes
    except ValueError:
        pass

    # Docker / custom deployment — check for profile path
    if env_path.parent.name == "profiles":
        return env_path.parent.parent
    return env_path


def display_sidekick_home() -> str:
    """Return a user-friendly display string for the current home directory.

    Uses ``~/`` shorthand for readability::

        default:  ``~/.sidekick``
        profile:  ``~/.sidekick/profiles/coder``
        custom:   ``/opt/sidekick-custom``

    Falls back to ``~/.sidekick`` display when no sidekick home exists yet.
    """
    home = get_sidekick_home()
    try:
        return "~/" + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)


# ─── Sidekick-specific path helpers ───────────────────────────────────────────

def get_sidekick_dir(new_subpath: str, old_name: str) -> Path:
    """Resolve a Sidekick subdirectory with backward compatibility.

    Delegates to ``get_hermes_dir()`` which already handles the old-vs-new
    layout logic, but uses ``get_sidekick_home()`` as the base.
    """
    home = get_sidekick_home()
    old_path = home / old_name
    if old_path.exists():
        return old_path
    return home / new_subpath


def get_optional_skills_dir(default: Path | None = None) -> Path:
    """Return the optional-skills directory, honoring package-manager wrappers.

    Checks ``SIDEKICK_OPTIONAL_SKILLS`` first, then ``HERMES_OPTIONAL_SKILLS``,
    then falls back to ``get_sidekick_home() / "optional-skills"``.
    """
    override = os.getenv("SIDEKICK_OPTIONAL_SKILLS", "").strip()
    if override:
        return Path(override)
    # Legacy fallback
    return _get_optional_skills_dir_original(default)


def get_subprocess_home_sidekick() -> str | None:
    """Return a per-profile HOME directory for subprocesses, or None.

    Uses ``SIDEKICK_HOME`` primary, ``HERMES_HOME`` secondary.
    Mirrors ``get_subprocess_home()`` in ``hermes_constants``.
    """
    home = os.getenv("SIDEKICK_HOME") or os.getenv("HERMES_HOME")
    if not home:
        return None
    profile_home = os.path.join(home, "home")
    if os.path.isdir(profile_home):
        return profile_home
    return None


def get_config_path() -> Path:
    """Return the path to ``config.yaml`` under the Sidekick home."""
    return get_sidekick_home() / "config.yaml"


def get_skills_dir() -> Path:
    """Return the path to the skills directory under the Sidekick home."""
    return get_sidekick_home() / "skills"


def get_env_path() -> Path:
    """Return the path to ``.env`` under the Sidekick home."""
    return get_sidekick_home() / ".env"
