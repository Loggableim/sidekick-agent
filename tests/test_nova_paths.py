import importlib
from pathlib import Path

from web.api.nova_paths import (
    get_nova_session_start_path,
    get_nova_space_root,
    get_nova_state_snapshot_path,
)


def test_nova_paths_prefer_sidekick_home(monkeypatch, tmp_path):
    sidekick_home = tmp_path / "sidekick-home"
    monkeypatch.setenv("SIDEKICK_HOME", str(sidekick_home))

    assert get_nova_space_root() == sidekick_home / "spaces" / "nova"
    assert get_nova_session_start_path() == sidekick_home / "spaces" / "nova" / "session_start.py"
    assert get_nova_state_snapshot_path() == Path(__file__).resolve().parents[1] / "nova" / "state_snapshot.py"


def test_nova_paths_fallback_to_user_home(monkeypatch, tmp_path):
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert get_nova_space_root() == tmp_path / ".sidekick" / "spaces" / "nova"


def test_nova_paths_follow_active_profile_after_import(monkeypatch, tmp_path):
    import sys

    import_home = tmp_path / "import-home"
    active_home = tmp_path / "active-home"

    monkeypatch.setenv("SIDEKICK_HOME", str(import_home))

    sys.modules.pop("web.api.nova_paths", None)
    nova_paths = importlib.import_module("web.api.nova_paths")
    profiles = importlib.import_module("web.api.profiles")

    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "coder")
    monkeypatch.setattr(profiles, "get_active_profile_home", lambda: active_home)

    assert nova_paths.get_nova_space_root() == active_home / "spaces" / "nova"
    assert nova_paths.get_nova_session_start_path() == active_home / "spaces" / "nova" / "session_start.py"
    assert nova_paths.get_nova_state_snapshot_path() == Path(__file__).resolve().parents[1] / "nova" / "state_snapshot.py"
