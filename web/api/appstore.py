"""
Sidekick Appstore Backend — manifest discovery, install/uninstall, status checks.

Provides API-callable functions that read app manifests from
``home/appstore/*.json``, apply env/config changes, and manage
installation state via ``home/appstore/.installed.json``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Manifest cache (T27) — simple dict-based cache with 30-second TTL
# ---------------------------------------------------------------------------
_MANIFEST_CACHE: dict = {}
_MANIFEST_CACHE_TTL = 30  # seconds


def _get_cached_manifests() -> list[dict] | None:
    """Return cached manifests if still fresh, else ``None``."""
    entry = _MANIFEST_CACHE.get("data")
    ts = _MANIFEST_CACHE.get("ts", 0.0)
    if entry is not None and (time.time() - ts) < _MANIFEST_CACHE_TTL:
        return entry
    return None


def _set_cached_manifests(manifests: list[dict]) -> None:
    """Store manifests in cache with current timestamp."""
    _MANIFEST_CACHE["data"] = manifests
    _MANIFEST_CACHE["ts"] = time.time()


def _invalidate_manifest_cache() -> None:
    """Clear the manifest cache (called on install / uninstall / submit)."""
    _MANIFEST_CACHE.clear()


# ---------------------------------------------------------------------------
# Paths (computed relative to this file's location in the repo)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # sidekick/
_SIDEKICK_HOME = Path(
    os.environ.get("SIDEKICK_HOME")
   
    or Path.home() / ".sidekick"
)
_HOME_DIR = _SIDEKICK_HOME
_APPS_DIR = _HOME_DIR / "appstore"
_INSTALLED_FILE = _APPS_DIR / ".installed.json"
_ENV_FILE = _SIDEKICK_HOME / ".env"
_CONFIG_FILE = _SIDEKICK_HOME / "config.yaml"

_VENV_PYTHON = _REPO_ROOT / ".venv" / "Scripts" / "python.exe"
if not _VENV_PYTHON.exists():
    _VENV_PYTHON = _REPO_ROOT / "venv" / "Scripts" / "python.exe"
_AGENT_DIR = _REPO_ROOT  # run_agent.py is in-repo now


# ---------------------------------------------------------------------------
# .env helpers  (line-based KEY=VALUE parsing, no external deps)
# ---------------------------------------------------------------------------

def _read_env() -> dict[str, str]:
    """Return all key-value pairs from ``~/.hermes/.env``."""
    if not _ENV_FILE.exists():
        return {}
    env: dict[str, str] = {}
    try:
        text = _ENV_FILE.read_text(encoding="utf-8")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
            if m:
                env[m.group(1)] = m.group(2)
    except OSError as exc:
        logger.warning("Failed to read %s: %s", _ENV_FILE, exc)
    return env


def _write_env(key: str, value: str) -> bool:
    """Set *key=value* in the .env file (idempotent). Returns True if changed."""
    existing = _read_env()
    if existing.get(key) == value:
        return False

    existing[key] = value
    lines: list[str] = []
    written = set()

    # Preserve existing order + comments if the file already exists
    if _ENV_FILE.exists():
        try:
            text = _ENV_FILE.read_text(encoding="utf-8")
            for line in text.splitlines():
                m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=.*", line.strip())
                if m and m.group(1) == key:
                    lines.append(f"{key}={value}")
                    written.add(key)
                else:
                    lines.append(line)
        except OSError:
            pass

    # Append any keys that weren't already in the file
    for k, v in existing.items():
        if k not in written:
            lines.append(f"{k}={v}")

    _ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _delete_env(key: str) -> bool:
    """Remove *key* from .env (idempotent). Returns True if anything changed."""
    if not _ENV_FILE.exists():
        return False
    removed = False
    try:
        text = _ENV_FILE.read_text(encoding="utf-8")
        new_lines: list[str] = []
        for line in text.splitlines():
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=.*", line.strip())
            if m and m.group(1) == key:
                removed = True
            else:
                new_lines.append(line)
        if removed:
            _ENV_FILE.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to delete env key %s: %s", key, exc)
    return removed


# ---------------------------------------------------------------------------
# Version comparison helper
# ---------------------------------------------------------------------------

def _version_tuple(v: str) -> tuple:
    """Parse a version string into a numeric tuple for comparison.

    Strips leading ``v``/``V`` prefix, splits on ``.``, converts each
    part to ``int`` (malformed parts become ``0``).

    Example:: ``\"v2.1.0\"`` → ``(2, 1, 0)``
    """
    v = v.lstrip("vV").strip()
    parts: list[int] = []
    for part in v.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


# ---------------------------------------------------------------------------
# config.yaml helpers  (uses yaml library available in the Sidekick venv)
# ---------------------------------------------------------------------------

def _patch_config(path_parts: list[str], value) -> bool:
    """Set a nested value in ``config.yaml``. Creates intermediate keys as needed.

    *path_parts* is a list like ``["services", "github", "enabled"]``.
    Returns True if the file was actually changed.
    """
    if not _CONFIG_FILE.exists():
        logger.warning("config.yaml not found at %s — cannot patch", _CONFIG_FILE)
        return False

    try:
        with open(str(_CONFIG_FILE), "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Failed to load config.yaml: %s", exc)
        return False

    # Navigate to the parent dict, creating intermediate dicts if needed
    current = config
    for part in path_parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]

    key = path_parts[-1]
    if key in current and current[key] == value:
        return False  # already set

    current[key] = value

    try:
        with open(str(_CONFIG_FILE), "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    except Exception as exc:
        logger.warning("Failed to write config.yaml: %s", exc)
        return False

    return True


def _unpatch_config(path_parts: list[str]) -> bool:
    """Remove a nested key from ``config.yaml`` (revert install changes).

    Returns True if something was removed.
    """
    if not _CONFIG_FILE.exists():
        return False

    try:
        with open(str(_CONFIG_FILE), "r", encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except Exception as exc:
        logger.warning("Failed to load config.yaml: %s", exc)
        return False

    current = config
    for part in path_parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return False  # path does not exist
        current = current[part]

    key = path_parts[-1]
    if not isinstance(current, dict) or key not in current:
        return False

    del current[key]

    try:
        with open(str(_CONFIG_FILE), "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
    except Exception as exc:
        logger.warning("Failed to write config.yaml during unpatch: %s", exc)
        return False

    return True


# ---------------------------------------------------------------------------
# Sidekick CLI subprocess helper
# ---------------------------------------------------------------------------

def _run_hermes_cli(args: list[str]) -> tuple[int, str]:
    """Run the Sidekick CLI via the venv Python and return ``(returncode, stdout)``."""
    cmd = [str(_VENV_PYTHON), "-m", "sidekick_cli.main"] + args
    env = os.environ.copy()

    # Ensure PYTHONPATH includes the agent directory so hermes_cli resolves
    agent_dir = str(_AGENT_DIR)
    pythonpath = env.get("PYTHONPATH", "")
    paths = [p for p in pythonpath.split(os.pathsep) if p]
    if agent_dir not in paths:
        paths.insert(0, agent_dir)
    env["PYTHONPATH"] = os.pathsep.join(paths)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        return result.returncode, result.stdout.strip()
    except subprocess.TimeoutExpired:
        logger.warning("Sidekick CLI timed out after 30s: %s", " ".join(args))
        return -1, "timeout"
    except Exception as exc:
        logger.warning("Sidekick CLI failed: %s — %s", " ".join(args), exc)
        return -1, str(exc)


# ---------------------------------------------------------------------------
# Installation tracking
# ---------------------------------------------------------------------------

def _load_installed() -> dict:
    """Load the ``.installed.json`` tracking file."""
    if not _INSTALLED_FILE.exists():
        return {}
    try:
        return json.loads(_INSTALLED_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load %s: %s", _INSTALLED_FILE, exc)
        return {}


def _save_installed(data: dict) -> None:
    """Write the ``.installed.json`` tracking file."""
    _INSTALLED_FILE.parent.mkdir(parents=True, exist_ok=True)
    _INSTALLED_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def discover_manifests() -> list[dict]:
    """Read all ``*.json`` files from ``home/appstore/`` and return them sorted by name.

    Results are cached for ``_MANIFEST_CACHE_TTL`` seconds (T27).
    Call ``_invalidate_manifest_cache()`` after writes.
    """
    cached = _get_cached_manifests()
    if cached is not None:
        return cached

    manifests: list[dict] = []
    if not _APPS_DIR.exists():
        logger.warning("Appstore directory not found: %s", _APPS_DIR)
        return manifests

    for p in sorted(_APPS_DIR.glob("*.json")):
        if p.name == ".installed.json":
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("key"):
                manifests.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping invalid manifest %s: %s", p.name, exc)

    manifests.sort(key=lambda m: (m.get("name") or m.get("key", "")))
    _set_cached_manifests(manifests)
    return manifests


def _is_installed(manifest: dict) -> bool:
    """Check whether all ``env_writes`` keys exist in the current .env."""
    env_writes = manifest.get("env_writes", {})
    if not env_writes:
        # If there are no env_writes, consider it installed if it's in .installed.json
        installed = _load_installed()
        return manifest.get("key") in installed
    current_env = _read_env()
    return all(k in current_env for k in env_writes)


def get_install_status(manifest_key: str) -> dict:
    """Return install status for a single app.

    Returns::

        {
            "installed": bool,
            "version_installed": str | None,
            "version_available": str,
        }
    """
    manifests = discover_manifests()
    manifest = next((m for m in manifests if m.get("key") == manifest_key), None)
    if not manifest:
        return {
            "installed": False,
            "version_installed": None,
            "version_available": "unknown",
        }

    installed_records = _load_installed()
    record = installed_records.get(manifest_key, {})
    version_installed = record.get("version") if _is_installed(manifest) else None

    return {
        "installed": _is_installed(manifest),
        "version_installed": version_installed,
        "version_available": manifest.get("version", "unknown"),
    }


def get_all_status() -> dict:
    """Return status for every available app.

    Returns::

        {
            "apps": [
                {
                    "key": "...",
                    "name": "...",
                    "icon": "...",
                    "version": "...",
                    ...manifest fields...
                    "status": {...install status...},
                },
                ...
            ],
            "installed_count": N,
            "available_count": N,
        }
    """
    manifests = discover_manifests()
    installed_records = _load_installed()
    apps: list[dict] = []
    installed_count = 0

    for m in manifests:
        key = m.get("key", "")
        installed = _is_installed(m)
        record = installed_records.get(key, {})
        version_installed = record.get("version") if installed else None

        app_entry = dict(m)  # copy manifest
        app_entry["status"] = {
            "installed": installed,
            "version_installed": version_installed,
            "version_available": m.get("version", "unknown"),
        }
        apps.append(app_entry)
        if installed:
            installed_count += 1

    return {
        "apps": apps,
        "installed_count": installed_count,
        "available_count": len(manifests),
    }


def install_app(manifest_key: str, values: dict) -> dict:
    """Install an app from the appstore.

    1. Load the manifest.
    2. Write all ``env_writes`` keys from *values* into ``.env``.
    3. Apply ``config_changes`` to ``config.yaml``.
    4. Enable tools via ``sidekick tools enable <name>``.
    5. Restart gateway if ``gateway_restart`` is set.
    6. Record installation in ``.installed.json``.

    Returns::

        {
            "success": bool,
            "changed_files": [str, ...],
            "gateway_restarted": bool,
            "error": str | None,
        }
    """
    manifests = discover_manifests()
    manifest = next((m for m in manifests if m.get("key") == manifest_key), None)
    if not manifest:
        return {
            "success": False,
            "changed_files": [],
            "gateway_restarted": False,
            "error": f"Manifest '{manifest_key}' not found",
        }

    changed_files: list[str] = []
    errors: list[str] = []

    # ---- 1. Env writes ----
    env_writes: dict = manifest.get("env_writes", {})
    for env_key in env_writes:
        val = values.get(env_key, "")
        try:
            if _write_env(env_key, val):
                changed_files.append(f".env#{env_key}")
        except Exception as exc:
            errors.append(f"Failed to write env {env_key}: {exc}")

    # ---- 2. Config changes ----
    config_changes: list[dict] = manifest.get("config_changes", [])
    for change in config_changes:
        path_str: str = change.get("path", "")
        val = change.get("value")
        if not path_str:
            continue
        path_parts = path_str.split(".")
        try:
            if _patch_config(path_parts, val):
                changed_files.append(f"config.yaml#{path_str}")
        except Exception as exc:
            errors.append(f"Failed to patch config {path_str}: {exc}")

    # ---- 3. Enable tools ----
    tools_to_enable: list[str] = manifest.get("tools_enable", [])
    for tool_name in tools_to_enable:
        try:
            rc, out = _run_hermes_cli(["tools", "enable", tool_name])
            if rc != 0:
                errors.append(f"Failed to enable tool '{tool_name}': {out}")
        except Exception as exc:
            errors.append(f"Exception enabling tool '{tool_name}': {exc}")

    # ---- 4. Gateway restart ----
    gateway_restarted = False
    if manifest.get("gateway_restart", False):
        try:
            rc, out = _run_hermes_cli(["gateway", "restart"])
            if rc == 0:
                gateway_restarted = True
                changed_files.append("gateway (restarted)")
            else:
                errors.append(f"Gateway restart failed: {out}")
        except Exception as exc:
            errors.append(f"Exception restarting gateway: {exc}")

    # ---- 5. Record installation ----
    installed = _load_installed()
    installed[manifest_key] = {
        "version": manifest.get("version", "unknown"),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "values": values,
    }
    try:
        _save_installed(installed)
        changed_files.append(".installed.json")
    except Exception as exc:
        errors.append(f"Failed to save installation record: {exc}")

    # Invalidate manifest cache (T27)
    _invalidate_manifest_cache()

    return {
        "success": len(errors) == 0,
        "changed_files": changed_files,
        "gateway_restarted": gateway_restarted,
        "error": "; ".join(errors) if errors else None,
    }


def uninstall_app(manifest_key: str) -> dict:
    """Uninstall an app.

    1. Load the manifest.
    2. Remove all ``env_writes`` keys from ``.env``.
    3. Revert ``config_changes`` (remove keys from ``config.yaml``).
    4. Disable tools via ``sidekick tools disable <name>``.
    5. Remove from ``.installed.json``.

    Returns::

        {
            "success": bool,
            "error": str | None,
            "status": int | None,  # 404 if not installed, omitted otherwise
        }
    """
    manifests = discover_manifests()
    manifest = next((m for m in manifests if m.get("key") == manifest_key), None)
    errors: list[str] = []

    # ---- 0. Check if app is actually installed ----
    installed = _load_installed()
    if manifest_key not in installed:
        return {
            "success": False,
            "error": f"App '{manifest_key}' is not installed",
            "status": 404,
        }

    if manifest:
        # ---- 1. Remove env writes ----
        env_writes: dict = manifest.get("env_writes", {})
        for env_key in env_writes:
            try:
                _delete_env(env_key)
            except Exception as exc:
                errors.append(f"Failed to delete env {env_key}: {exc}")

        # ---- 2. Revert config changes ----
        config_changes: list[dict] = manifest.get("config_changes", [])
        for change in config_changes:
            path_str: str = change.get("path", "")
            if not path_str:
                continue
            path_parts = path_str.split(".")
            try:
                _unpatch_config(path_parts)
            except Exception as exc:
                errors.append(f"Failed to revert config {path_str}: {exc}")

        # ---- 3. Disable tools ----
        tools_to_disable: list[str] = manifest.get("tools_enable", [])
        for tool_name in tools_to_disable:
            try:
                rc, out = _run_hermes_cli(["tools", "disable", tool_name])
                if rc != 0:
                    errors.append(f"Failed to disable tool '{tool_name}': {out}")
            except Exception as exc:
                errors.append(f"Exception disabling tool '{tool_name}': {exc}")

    # ---- 4. Remove installation record ----
    if manifest_key in installed:
        del installed[manifest_key]
        try:
            _save_installed(installed)
        except Exception as exc:
            errors.append(f"Failed to update installation record: {exc}")

    # Invalidate manifest cache (T27)
    _invalidate_manifest_cache()

    return {
        "success": len(errors) == 0,
        "error": "; ".join(errors) if errors else None,
    }


def get_updates() -> list[dict]:
    """Compare manifest versions with installed versions for every installed app.

    Returns a list with one entry per installed app::

        [
            {
                "key": "...",
                "name": "...",
                "version_installed": "...",
                "version_available": "...",
                "update_available": bool,
            },
            ...
        ]

    ``update_available`` is ``True`` when the manifest version is strictly
    greater than the installed version (semver-like numeric comparison,
    ``v``/``V`` prefixes are stripped automatically).
    """
    manifests = discover_manifests()
    installed_records = _load_installed()
    updates: list[dict] = []

    for m in manifests:
        key = m.get("key", "")
        record = installed_records.get(key)
        if not record:
            continue
        version_installed = record.get("version", "")
        version_available = m.get("version", "")
        if not version_installed:
            continue
        if version_available:
            update_available = _version_tuple(version_available) > _version_tuple(version_installed)
        else:
            update_available = False
        updates.append({
            "key": key,
            "name": m.get("name", key),
            "version_installed": version_installed,
            "version_available": version_available,
            "update_available": update_available,
        })

    return updates


def get_sdk_docs() -> dict:
    """Return the Appstore SDK markdown for in-app documentation."""
    sdk_path = _APPS_DIR / "SDK.md"
    if not sdk_path.exists() or not sdk_path.is_file():
        return {
            "success": False,
            "markdown": "",
            "error": "SDK.md not found",
        }
    try:
        return {
            "success": True,
            "markdown": sdk_path.read_text(encoding="utf-8"),
            "path": str(sdk_path),
            "error": None,
        }
    except OSError as exc:
        return {
            "success": False,
            "markdown": "",
            "error": str(exc),
        }


# ---------------------------------------------------------------------------
# T19 – Plugin-Einreichen (submit a new plugin manifest)
# ---------------------------------------------------------------------------

_SUBMITTED_DIR = _APPS_DIR / "submitted"


def submit_plugin(manifest: dict) -> dict:
    """Submit (einreichen) a new plugin manifest for review.

    Validates required fields (key, name, icon, cat, setup_steps),
    checks that ``key`` only contains ``[a-z0-9_]``, and writes the
    manifest to ``home/appstore/submitted/{key}.json``.

    Returns::

        {
            "success": bool,
            "message": str,
            "path": str | None,
            "error": str | None,
        }
    """
    # ---- Validate required fields ----
    required = ["key", "name", "icon", "cat", "setup_steps"]
    missing = [f for f in required if f not in manifest or not manifest[f]]
    if missing:
        return {
            "success": False,
            "message": "Validation failed",
            "path": None,
            "error": f"Missing required fields: {', '.join(missing)}",
        }

    # ---- Validate key format ----
    key = str(manifest.get("key", ""))
    if not re.match(r"^[a-z0-9_]+$", key):
        return {
            "success": False,
            "message": "Validation failed",
            "path": None,
            "error": "Key must only contain lowercase a-z, 0-9, and underscores",
        }

    # ---- Save to submitted/ directory ----
    try:
        _SUBMITTED_DIR.mkdir(parents=True, exist_ok=True)
        dest = _SUBMITTED_DIR / f"{key}.json"
        dest.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        return {
            "success": False,
            "message": "Save failed",
            "path": None,
            "error": f"Could not write manifest: {exc}",
        }

    # Invalidate manifest cache (T27) since a new manifest was added
    _invalidate_manifest_cache()

    return {
        "success": True,
        "message": "Plugin eingereicht",
        "path": str(dest),
        "error": None,
    }


# ---------------------------------------------------------------------------
# T8 – Bulk-Update: update all installed apps
# ---------------------------------------------------------------------------


def update_all() -> dict:
    """Update all installed apps that have a newer version available.

    Iterates over every entry returned by :func:`get_updates` where
    ``update_available`` is ``True`` and re-runs ``install_app`` for each
    using the previously stored values.

    Returns::

        {
            "success": true,
            "updated": [
                {"key": "...", "name": "...", "old_version": "...", "new_version": "..."},
                ...
            ],
            "failed": [
                {"key": "...", "error": "..."},
                ...
            ],
            "total": N,
        }
    """
    updates = get_updates()
    updated: list[dict] = []
    failed: list[dict] = []
    installed = _load_installed()

    for u in updates:
        if not u.get("update_available"):
            continue
        key = u["key"]
        record = installed.get(key, {})
        values = record.get("values", {})

        result = install_app(key, values)
        if result.get("success"):
            updated.append({
                "key": key,
                "name": u.get("name", key),
                "old_version": u.get("version_installed"),
                "new_version": u.get("version_available"),
            })
        else:
            failed.append({
                "key": key,
                "error": result.get("error", "unknown error"),
            })

    return {
        "success": True,
        "updated": updated,
        "failed": failed,
        "total": len(updated) + len(failed),
    }
