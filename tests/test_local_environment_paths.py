from tools.environments import local


def test_windows_posix_path_to_native(monkeypatch):
    monkeypatch.setattr(local, "_IS_WINDOWS", True)

    assert local._windows_posix_path_to_native("/c/sidekick/home/spaces/nova") == (
        "C:\\sidekick\\home\\spaces\\nova"
    )
    assert local._windows_posix_path_to_native("/d") == "D:\\"
    assert local._windows_posix_path_to_native("C:\\sidekick") == "C:\\sidekick"


def test_non_windows_posix_path_is_unchanged(monkeypatch):
    monkeypatch.setattr(local, "_IS_WINDOWS", False)

    assert local._windows_posix_path_to_native("/c/sidekick/home") == "/c/sidekick/home"
