from pathlib import Path

from web.api.nova_paths import (
    get_nova_session_start_path,
    get_nova_space_root,
    get_nova_state_snapshot_path,
)


def test_nova_paths_prefer_sidekick_home(monkeypatch, tmp_path):
    sidekick_home = tmp_path / "sidekick-home"
    monkeypatch.setenv("SIDEKICK_HOME", str(sidekick_home))
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "legacy-home"))

    assert get_nova_space_root() == sidekick_home / "spaces" / "nova"
    assert get_nova_session_start_path() == sidekick_home / "spaces" / "nova" / "session_start.py"
    assert get_nova_state_snapshot_path() == sidekick_home / "spaces" / "nova" / "state_snapshot.py"


def test_nova_paths_fallback_to_legacy_home(monkeypatch, tmp_path):
    legacy_home = tmp_path / "legacy-home"
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(legacy_home))

    assert get_nova_space_root() == legacy_home / "spaces" / "nova"


def test_nova_paths_fallback_to_repo_home(monkeypatch):
    monkeypatch.delenv("SIDEKICK_HOME", raising=False)
    monkeypatch.delenv("HERMES_HOME", raising=False)

    assert get_nova_space_root() == Path(__file__).resolve().parents[1] / "home" / "spaces" / "nova"
