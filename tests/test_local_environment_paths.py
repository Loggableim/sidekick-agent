from tools.environments import local
from tools.environments.local import LocalEnvironment


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


def test_local_environment_recovers_missing_cwd_for_current_command(tmp_path):
    env = LocalEnvironment(cwd=str(tmp_path), timeout=10)
    try:
        env.cwd = str(tmp_path / "missing" / "child")

        result = env.execute("python -c \"print('cwd recovered')\"", timeout=20)

        assert result["returncode"] == 0
        assert "cwd recovered" in result["output"]
    finally:
        env.cleanup()
