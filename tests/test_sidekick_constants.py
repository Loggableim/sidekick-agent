import sidekick_constants


def test_legacy_directory_alias_delegates_without_recursion(monkeypatch, tmp_path):
    monkeypatch.setattr(sidekick_constants, "_get_sidekick_dir", lambda new, old: tmp_path / new)

    assert sidekick_constants.get_sidekick_dir("platforms/pairing", "pairing") == tmp_path / "platforms/pairing"


def test_default_root_alias_delegates_without_recursion(monkeypatch, tmp_path):
    monkeypatch.setattr(sidekick_constants, "_get_default_sidekick_root", lambda: tmp_path)

    assert sidekick_constants.get_default_sidekick_root() == tmp_path
