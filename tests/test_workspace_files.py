from io import BytesIO
import importlib
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode
import zipfile


def test_list_dir_handles_file_deleted_between_is_file_and_stat(monkeypatch, tmp_path):
    from web.api.workspace import list_dir

    target = tmp_path / "gone.txt"
    target.write_text("temporary\n", encoding="utf-8")

    path_cls = type(tmp_path)
    original_is_file = path_cls.is_file
    original_stat = path_cls.stat

    def fake_is_file(self):
        if self.name == "gone.txt":
            return True
        return original_is_file(self)

    def fake_stat(self, *args, **kwargs):
        if self.name == "gone.txt":
            raise FileNotFoundError(str(self))
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(path_cls, "is_file", fake_is_file)
    monkeypatch.setattr(path_cls, "stat", fake_stat)

    entries = list_dir(tmp_path)

    assert entries == [
        {
            "name": "gone.txt",
            "path": "gone.txt",
            "type": "file",
            "size": None,
        }
    ]


def test_clean_workspace_list_filters_other_profile_directories(monkeypatch, tmp_path):
    import sys

    active_home = tmp_path / "sidekick"
    other_profile_path = active_home / "profiles" / "other" / "workspace"
    other_profile_path.mkdir(parents=True)
    keep_path = tmp_path / "shared-workspace"
    keep_path.mkdir()

    monkeypatch.setenv("SIDEKICK_HOME", str(active_home))

    sys.modules.pop("web.api.profiles", None)
    sys.modules.pop("web.api.workspace", None)
    profiles = importlib.import_module("web.api.profiles")
    workspace = importlib.import_module("web.api.workspace")

    monkeypatch.setattr(profiles, "get_active_profile_home", lambda: active_home / "profiles" / "webui")

    cleaned = workspace._clean_workspace_list(
        [
            {"path": str(other_profile_path), "name": "other"},
            {"path": str(keep_path), "name": "shared"},
        ]
    )

    assert cleaned == [
        {"path": str(keep_path.resolve()), "name": "shared"},
    ]


def test_list_dir_rejects_windows_alternate_data_stream_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    parsed = SimpleNamespace(query=urlencode({"session_id": "s1", "path": "notes.txt:ads"}))

    response = routes._handle_list_dir(object(), parsed)

    assert response["status"] == 400


def test_file_rename_rejects_windows_absolute_new_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.txt"
    source.write_text("keep me\n", encoding="utf-8")
    outside = tmp_path / "outside.txt"

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_rename(
        object(),
        {"session_id": "s1", "path": "source.txt", "new_name": str(outside)},
    )

    assert response["status"] == 400
    assert source.read_text(encoding="utf-8") == "keep me\n"
    assert not outside.exists()


def test_file_rename_renames_symlink_not_target(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("keep target\n", encoding="utf-8")
    link = workspace / "link.txt"
    link.symlink_to(target)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_rename(
        object(),
        {"session_id": "s1", "path": "link.txt", "new_name": "renamed-link.txt"},
    )

    renamed = workspace / "renamed-link.txt"
    assert response["status"] == 200
    assert not link.exists()
    assert renamed.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep target\n"


def test_file_rename_allows_broken_symlink(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing_target = workspace / "missing.txt"
    link = workspace / "broken-link.txt"
    link.symlink_to(missing_target)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_rename(
        object(),
        {"session_id": "s1", "path": "broken-link.txt", "new_name": "renamed-broken-link.txt"},
    )

    renamed = workspace / "renamed-broken-link.txt"
    assert response["status"] == 200
    assert not link.is_symlink()
    assert renamed.is_symlink()
    assert not missing_target.exists()


def test_file_rename_rejects_windows_device_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.txt"
    source.write_text("keep me\n", encoding="utf-8")

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_rename(
        object(),
        {"session_id": "s1", "path": "source.txt", "new_name": "LPT1.txt"},
    )

    assert response["status"] == 400
    assert source.read_text(encoding="utf-8") == "keep me\n"
    assert not (workspace / "LPT1.txt").exists()


def test_file_create_reports_parent_file_conflict(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent = workspace / "parent"
    parent.write_text("not a directory\n", encoding="utf-8")

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_create(
        object(),
        {"session_id": "s1", "path": "parent/child.txt", "content": "new\n"},
    )

    assert response["status"] == 400
    assert parent.read_text(encoding="utf-8") == "not a directory\n"
    assert not (workspace / "parent" / "child.txt").exists()


def test_file_create_rejects_broken_symlink_path(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing_target = workspace / "missing-target.txt"
    link = workspace / "new.txt"
    link.symlink_to(missing_target)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_create(
        object(),
        {"session_id": "s1", "path": "new.txt", "content": "created\n"},
    )

    assert response["status"] == 400
    assert link.is_symlink()
    assert not missing_target.exists()


def test_file_create_rejects_windows_device_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_create(
        object(),
        {"session_id": "s1", "path": "CON.txt", "content": "device\n"},
    )

    assert response["status"] == 400
    assert not (workspace / "CON.txt").exists()


def test_file_create_rejects_windows_alternate_data_stream_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_create(
        object(),
        {"session_id": "s1", "path": "notes.txt:ads", "content": "hidden\n"},
    )

    assert response["status"] == 400
    assert not (workspace / "notes.txt").exists()


def test_file_create_allows_symlink_workspace_root(monkeypatch, tmp_path):
    from web.api import routes

    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    workspace_link = tmp_path / "workspace-link"
    workspace_link.symlink_to(real_workspace, target_is_directory=True)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace_link)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_create(
        object(),
        {"session_id": "s1", "path": "created.txt", "content": "created\n"},
    )

    assert response["status"] == 200
    assert (real_workspace / "created.txt").read_text(encoding="utf-8") == "created\n"


def test_file_rename_symlink_in_symlink_workspace_root_returns_relative_path(monkeypatch, tmp_path):
    from web.api import routes

    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    workspace_link = tmp_path / "workspace-link"
    workspace_link.symlink_to(real_workspace, target_is_directory=True)
    target = real_workspace / "target.txt"
    target.write_text("keep target\n", encoding="utf-8")
    link = workspace_link / "link.txt"
    link.symlink_to(target)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace_link)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_rename(
        object(),
        {"session_id": "s1", "path": "link.txt", "new_name": "renamed-link.txt"},
    )

    renamed = workspace_link / "renamed-link.txt"
    assert response["status"] == 200
    assert response["payload"]["new_path"] == "renamed-link.txt"
    assert renamed.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep target\n"


def test_workspace_write_rejects_broken_symlink_path(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing_target = workspace / "missing-target.txt"
    link = workspace / "generated.txt"
    link.symlink_to(missing_target)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_workspace_write(
        object(),
        {"session_id": "s1", "path": "generated.txt", "content": "generated\n"},
    )

    assert response["status"] == 400
    assert link.is_symlink()
    assert not missing_target.exists()


def test_workspace_write_rejects_windows_device_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_workspace_write(
        object(),
        {"session_id": "s1", "path": "nested/NUL.txt", "content": "device\n"},
    )

    assert response["status"] == 400
    assert not (workspace / "nested").exists()


def test_workspace_write_allows_symlink_workspace_root(monkeypatch, tmp_path):
    from web.api import routes

    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    workspace_link = tmp_path / "workspace-link"
    workspace_link.symlink_to(real_workspace, target_is_directory=True)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace_link)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_workspace_write(
        object(),
        {"session_id": "s1", "path": "nested/generated.txt", "content": "generated\n"},
    )

    assert response["status"] == 200
    assert response["payload"]["path"] == str(Path("nested") / "generated.txt")
    assert (real_workspace / "nested" / "generated.txt").read_text(encoding="utf-8") == "generated\n"


def test_file_save_rejects_symlink_target(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    link = workspace / "link.txt"
    link.symlink_to(outside)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_save(
        object(),
        {"session_id": "s1", "path": "link.txt", "content": "changed\n"},
    )

    assert response["status"] == 400
    assert link.is_symlink()
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_file_save_rejects_windows_alternate_data_stream_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    notes = workspace / "notes.txt"
    notes.write_text("visible\n", encoding="utf-8")

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_save(
        object(),
        {"session_id": "s1", "path": "notes.txt:ads", "content": "hidden\n"},
    )

    assert response["status"] == 400
    assert notes.read_text(encoding="utf-8") == "visible\n"


def test_create_dir_rejects_broken_symlink_path(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    missing_target = workspace / "missing-dir"
    link = workspace / "new-dir"
    link.symlink_to(missing_target, target_is_directory=True)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_create_dir(
        object(),
        {"session_id": "s1", "path": "new-dir"},
    )

    assert response["status"] == 400
    assert link.is_symlink()
    assert not missing_target.exists()


def test_create_dir_rejects_windows_device_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_create_dir(
        object(),
        {"session_id": "s1", "path": "AUX"},
    )

    assert response["status"] == 400
    assert not (workspace / "AUX").exists()


def test_create_dir_allows_symlink_workspace_root(monkeypatch, tmp_path):
    from web.api import routes

    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    workspace_link = tmp_path / "workspace-link"
    workspace_link.symlink_to(real_workspace, target_is_directory=True)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace_link)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_create_dir(
        object(),
        {"session_id": "s1", "path": "nested/dir"},
    )

    assert response["status"] == 200
    assert response["payload"]["path"] == str(Path("nested") / "dir")
    assert (real_workspace / "nested" / "dir").is_dir()


def test_file_delete_rejects_workspace_root(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = workspace / "keep.txt"
    sentinel.write_text("do not delete\n", encoding="utf-8")

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_delete(
        object(),
        {"session_id": "s1", "path": ".", "recursive": True},
    )

    assert response["status"] == 400
    assert workspace.is_dir()
    assert sentinel.read_text(encoding="utf-8") == "do not delete\n"


def test_file_delete_rejects_windows_alternate_data_stream_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt:ads"
    target.write_text("keep stream-like path\n", encoding="utf-8")

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_delete(
        object(),
        {"session_id": "s1", "path": "notes.txt:ads"},
    )

    assert response["status"] == 400
    assert target.read_text(encoding="utf-8") == "keep stream-like path\n"


def test_file_delete_removes_symlink_not_target(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("keep target\n", encoding="utf-8")
    link = workspace / "link.txt"
    link.symlink_to(target)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_delete(
        object(),
        {"session_id": "s1", "path": "link.txt"},
    )

    assert response["status"] == 200
    assert not link.exists()
    assert not link.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep target\n"


def test_file_delete_removes_workspace_symlink_to_outside_target(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep outside\n", encoding="utf-8")
    link = workspace / "outside-link.txt"
    link.symlink_to(outside)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_delete(
        object(),
        {"session_id": "s1", "path": "outside-link.txt"},
    )

    assert response["status"] == 200
    assert not link.exists()
    assert not link.is_symlink()
    assert outside.read_text(encoding="utf-8") == "keep outside\n"


def test_file_delete_removes_symlink_in_symlink_workspace_root(monkeypatch, tmp_path):
    from web.api import routes

    real_workspace = tmp_path / "real-workspace"
    real_workspace.mkdir()
    workspace_link = tmp_path / "workspace-link"
    workspace_link.symlink_to(real_workspace, target_is_directory=True)
    target = real_workspace / "target.txt"
    target.write_text("keep target\n", encoding="utf-8")
    link = workspace_link / "link.txt"
    link.symlink_to(target)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace_link)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_delete(
        object(),
        {"session_id": "s1", "path": "link.txt"},
    )

    assert response["status"] == 200
    assert not link.exists()
    assert not link.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep target\n"


def test_file_delete_rejects_raw_symlink_path_outside_workspace(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "target.txt"
    target.write_text("keep target\n", encoding="utf-8")
    outside_link = tmp_path / "outside-link.txt"
    outside_link.symlink_to(target)

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_delete(
        object(),
        {"session_id": "s1", "path": "../outside-link.txt"},
    )

    assert response["status"] == 400
    assert outside_link.is_symlink()
    assert target.read_text(encoding="utf-8") == "keep target\n"


def test_file_raw_rejects_path_traversal(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret\n", encoding="utf-8")

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    parsed = SimpleNamespace(query=urlencode({"session_id": "s1", "path": "../outside.txt"}))

    response = routes._handle_file_raw(object(), parsed)

    assert response["status"] == 400
    assert outside.read_text(encoding="utf-8") == "secret\n"


def test_file_raw_rejects_windows_alternate_data_stream_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt:ads"
    target.write_text("do not serve\n", encoding="utf-8")

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )
    monkeypatch.setattr(
        routes,
        "_serve_file_bytes",
        lambda *_args, **_kwargs: {"status": 200, "payload": {"served": True}},
    )

    parsed = SimpleNamespace(query=urlencode({"session_id": "s1", "path": "notes.txt:ads"}))

    response = routes._handle_file_raw(object(), parsed)

    assert response["status"] == 400


def test_file_read_rejects_windows_alternate_data_stream_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt:ads"
    target.write_text("do not read\n", encoding="utf-8")

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    parsed = SimpleNamespace(query=urlencode({"session_id": "s1", "path": "notes.txt:ads"}))

    response = routes._handle_file_read(object(), parsed)

    assert response["status"] == 400


def test_file_reveal_rejects_windows_alternate_data_stream_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "notes.txt:ads"
    target.write_text("do not reveal\n", encoding="utf-8")
    launched = []

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )
    monkeypatch.setattr(routes.subprocess, "Popen", lambda args: launched.append(args))

    response = routes._handle_file_reveal(
        object(),
        {"session_id": "s1", "path": "notes.txt:ads"},
    )

    assert response["status"] == 400
    assert launched == []


def test_file_path_rejects_windows_alternate_data_stream_name(monkeypatch, tmp_path):
    from web.api import routes

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    monkeypatch.setattr(routes, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(routes, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})
    monkeypatch.setattr(
        routes,
        "bad",
        lambda _handler, msg, status=400: {"status": status, "payload": {"error": str(msg)}},
    )

    response = routes._handle_file_path(
        object(),
        {"session_id": "s1", "path": "notes.txt:ads"},
    )

    assert response["status"] == 400


def test_upload_rejects_existing_symlink_target(monkeypatch, tmp_path):
    from web.api import upload

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (workspace / "link.txt").symlink_to(outside)

    boundary = "----sidekick-test"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="session_id"\r\n\r\n'
        "s1\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="link.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "uploaded\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    handler = SimpleNamespace(
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        rfile=BytesIO(body),
    )

    monkeypatch.setattr(upload, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(upload, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})

    response = upload.handle_upload(handler)

    assert response["status"] == 400
    assert outside.read_text(encoding="utf-8") == "outside\n"


def test_upload_rejects_negative_content_length(monkeypatch, tmp_path):
    from web.api import upload

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    boundary = "----sidekick-test"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="session_id"\r\n\r\n'
        "s1\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="payload.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        "uploaded\n"
        f"--{boundary}--\r\n"
    ).encode("utf-8")

    handler = SimpleNamespace(
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": "-1",
        },
        rfile=BytesIO(body),
    )

    monkeypatch.setattr(upload, "get_session", lambda _sid: SimpleNamespace(workspace=str(workspace)))
    monkeypatch.setattr(upload, "j", lambda _handler, payload, status=200, **_kw: {"status": status, "payload": payload})

    response = upload.handle_upload(handler)

    assert response["status"] == 400
    assert not (workspace / "payload.txt").exists()


def test_upload_sanitizer_rejects_windows_device_names():
    from web.api.upload import _sanitize_upload_name

    for filename in ("CON", "nul.txt", "COM1.log", "Lpt9"):
        try:
            _sanitize_upload_name(filename)
        except ValueError as exc:
            assert "filename" in str(exc).lower()
        else:
            raise AssertionError(f"expected reserved device name to be rejected: {filename}")


def test_extract_archive_rejects_symlink_destination(tmp_path):
    from web.api.upload import extract_archive

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "bundle").symlink_to(outside, target_is_directory=True)

    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("payload.txt", "external write\n")

    try:
        extract_archive(archive.getvalue(), "bundle.zip", workspace)
    except ValueError as exc:
        assert "symlink" in str(exc).lower()
    else:
        raise AssertionError("expected symlink archive destination to be rejected")

    assert not (outside / "payload.txt").exists()


def test_extract_archive_rejects_windows_device_member_name(tmp_path):
    from web.api.upload import extract_archive

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("nested/NUL.txt", "discarded on Windows\n")

    try:
        extract_archive(archive.getvalue(), "bundle.zip", workspace)
    except ValueError as exc:
        assert "filename" in str(exc).lower()
    else:
        raise AssertionError("expected reserved archive member name to be rejected")

    assert not (workspace / "bundle" / "nested").exists()


def test_extract_archive_rejects_windows_device_destination_name(tmp_path):
    from web.api.upload import extract_archive

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    archive = BytesIO()
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("payload.txt", "content\n")

    try:
        extract_archive(archive.getvalue(), "CON.zip", workspace)
    except ValueError as exc:
        assert "filename" in str(exc).lower()
    else:
        raise AssertionError("expected reserved archive destination name to be rejected")
