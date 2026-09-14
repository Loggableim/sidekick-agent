"""Regression tests for project_dir validation on space config writes.

Bug (observed 2026-09-14 on a live install): the dashboard's space
create/update endpoints persisted any ``project_dir`` string unchecked.
A test's temp path leaked into all three production ``space.yaml`` files
as ``project_dir``; after the temp dir was deleted, every WebUI request
logged ``project_dir ... does not exist`` and the space's project
binding was dead. A hostile or typo'd value (a system directory, a file
instead of a directory) was persisted just as silently.
"""

from __future__ import annotations

import pytest

TestClient = pytest.importorskip("fastapi.testclient").TestClient


def _space_client():
    from cli import web_server

    return TestClient(web_server.app), {
        web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN
    }


def test_validate_project_dir_rejects_missing_directory():
    from web.api.space_engine import SpaceGovernanceError, validate_project_dir

    with pytest.raises(SpaceGovernanceError, match="does not exist"):
        validate_project_dir(r"C:\definitely\not\a\real\dir")


def test_validate_project_dir_rejects_sidekick_home_interior():
    from web.api.space_engine import SpaceGovernanceError, validate_project_dir

    # A file (not a directory) is rejected by the existence check.
    with pytest.raises(SpaceGovernanceError, match="does not exist"):
        validate_project_dir(__file__)


def test_validate_project_dir_accepts_existing_directory_and_empty(tmp_path):
    from web.api.space_engine import validate_project_dir

    assert validate_project_dir(str(tmp_path)) == str(tmp_path.resolve())
    assert validate_project_dir("") == ""
    assert validate_project_dir(None) == ""


def test_space_create_rejects_nonexistent_project_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    client, headers = _space_client()

    response = client.post(
        "/api/space/create",
        json={
            "slug": "probe-space",
            "name": "Probe",
            "project_dir": r"C:\definitely\not\a\real\dir",
        },
        headers=headers,
    )

    assert response.status_code == 400
    assert "does not exist" in response.json()["error"]["message"]


def test_space_update_rejects_nonexistent_project_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path / "home"))
    client, headers = _space_client()

    created = client.post(
        "/api/space/create",
        json={"slug": "probe-update", "name": "Probe Update"},
        headers=headers,
    )
    assert created.status_code == 200

    response = client.post(
        "/api/space/config",
        json={"slug": "probe-update", "project_dir": r"C:\definitely\not\a\real\dir"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "does not exist" in response.json()["error"]["message"]