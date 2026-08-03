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

