from pathlib import Path


def test_enrollment_readiness_describes_legacy_space_without_mutating_it(tmp_path, monkeypatch):
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    project = tmp_path / "project"
    project.mkdir()
    space = space_engine.Space("aquarium-zentrum", "Aquarium")
    space.save_config({"name": "Aquarium", "project_dir": str(project)})
    before = space.config_path.read_bytes()

    readiness = space_engine.nova_enrollment_readiness(space, trusted_project_root=project)

    assert readiness["state"] == "blocked"
    assert readiness["ready"] is False
    assert readiness["reason_codes"] == ["space_id_missing", "yolo_not_enabled"]
    assert readiness["next_step_code"] == "persist_space_id"
    assert readiness["requires_explicit_write"] is True
    assert readiness["project_dir_configured"] is True
    assert readiness["trusted_root_verified"] is True
    assert space.config_path.read_bytes() == before
    assert space.load_config()["space_id"] == ""


def test_enrollment_readiness_requires_trusted_root_and_reports_missing_project(tmp_path, monkeypatch):
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("finanzjunkie", "Finanzjunkie")
    space.save_config({"name": "Finanzjunkie"}, mint_space_id=True)

    readiness = space_engine.nova_enrollment_readiness(space)

    assert readiness["state"] == "blocked"
    assert readiness["reason_codes"] == [
        "project_dir_missing",
        "trusted_workspace_unavailable",
        "yolo_not_enabled",
    ]
    assert readiness["next_step_code"] == "configure_project_dir"
    assert readiness["trusted_root_verified"] is False


def test_enrollment_readiness_is_ready_only_after_yolo_and_trusted_binding(tmp_path, monkeypatch):
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    project = tmp_path / "project"
    project.mkdir()
    space = space_engine.Space("aquarium-zentrum", "Aquarium")
    space.save_config({"name": "Aquarium", "project_dir": str(project)}, mint_space_id=True)
    config = space.load_config()
    space_engine.update_nova_management(
        space,
        yolo=True,
        enrolled=False,
        confirmation=None,
        trusted_project_root=project,
        actor="dashboard:" + "a" * 64,
    )

    readiness = space_engine.nova_enrollment_readiness(space, trusted_project_root=project)

    assert readiness["state"] == "ready"
    assert readiness["ready"] is True
    assert readiness["reason_codes"] == ["nova_enrollment_not_enabled"]
    assert readiness["next_step_code"] == "enroll_nova_management"
    assert readiness["space_id_persisted"] is True
    assert readiness["trusted_root_verified"] is True


def test_management_payload_exposes_readiness_without_persisting_legacy_fields(tmp_path, monkeypatch):
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    project = tmp_path / "project"
    project.mkdir()
    space = space_engine.Space("aquarium-zentrum", "Aquarium")
    space.save_config({"name": "Aquarium", "project_dir": str(project)})
    before = space.config_path.read_bytes()

    payload = web_server._nova_management_payload(space, project)

    assert payload["enrollment_readiness"]["next_step_code"] == "persist_space_id"
    assert payload["enrollment_readiness"]["reason_codes"] == [
        "space_id_missing",
        "yolo_not_enabled",
    ]
    assert space.config_path.read_bytes() == before