"""Test-only fixtures for environments with a restricted Windows temp root.

The hosted Windows runner can leave ``%TEMP%\\pytest-of-*`` with an ACL that
pytest cannot enumerate. Keep tests deterministic by putting their per-test
directories below the repository, where the test process has write access.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

import pytest


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    """Return an isolated, repository-local temporary directory."""

    root = Path(__file__).resolve().parent.parent / ".test-tmp"
    digest = hashlib.sha256(request.node.nodeid.encode("utf-8")).hexdigest()[:16]
    # A deterministic node-id path is unsafe on Windows: a locked SQLite
    # handle can make cleanup fail, causing a later invocation to reuse stale
    # rows and violate the supervisor one-run index. Keep each invocation
    # isolated even when cleanup of an earlier run was incomplete.
    path = root / f"test-{digest}-{os.getpid()}-{time.time_ns()}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        # Cleanup is best-effort: a locked file must not turn a passing test
        # into a teardown failure on Windows.
        shutil.rmtree(path, ignore_errors=True)


@pytest.fixture
def symlink_capable(tmp_path):
    """Skip symlink attacks only when the OS denies creating the test fixture."""
    target = tmp_path / "symlink-probe-target"
    link = tmp_path / "symlink-probe-link"
    target.touch()
    try:
        link.symlink_to(target)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink privilege is unavailable")
        raise
    finally:
        if link.is_symlink():
            link.unlink()
        target.unlink()


_LAUNCHER_ENV_VARS = (
    # Sidekick-Launcher.ps1 (Set-SidekickEnv) exports these for the running
    # WebUI. Agent terminals inherit them, so pytest runs from inside a
    # Sidekick session carry live runtime overrides that defeat the
    # SIDEKICK_HOME-based isolation these tests rely on. Clear them for every
    # test unless a test explicitly sets its own value.
    "SIDEKICK_WEBUI_STATE_DIR",
    "SIDEKICK_STATE_DIR",
    "SIDEKICK_WEBUI_AGENT_DIR",
    "SIDEKICK_WEBUI_PYTHON",
    "SIDEKICK_WEBUI_PORT",
    "SIDEKICK_WEBUI_HOST",
    "SIDEKICK_WEBUI_TLS_CERT",
    "SIDEKICK_WEBUI_TLS_KEY",
)


@pytest.fixture(autouse=True)
def _clear_launcher_env_overrides(monkeypatch):
    """Isolate tests from the running WebUI's launcher environment."""
    for name in _LAUNCHER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

