from __future__ import annotations


def test_space_scan_skips_empty_default_alias(monkeypatch, tmp_path):
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    nova = spaces_root / "nova"
    alias = spaces_root / "novaspace"
    nova.mkdir(parents=True)
    alias.mkdir(parents=True)
    (alias / "agents").mkdir()
    (alias / "memory").mkdir()
    (alias / "sessions").mkdir()

    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(space_engine, "DEFAULT_SPACE_SLUG", "nova")
    monkeypatch.setattr(space_engine, "DEFAULT_SPACE_NAME", "Nova")
    monkeypatch.setattr(space_engine, "DEFAULT_SPACE_ALIASES", {"novaspace"})
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)

    space_engine.Space("nova", "Nova").save_config({"name": "Nova"})

    spaces = space_engine.get_all_spaces()

    assert [space.slug for space in spaces] == ["nova"]


def test_get_or_create_space_normalizes_default_alias(monkeypatch, tmp_path):
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"

    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    monkeypatch.setattr(space_engine, "DEFAULT_SPACE_SLUG", "nova")
    monkeypatch.setattr(space_engine, "DEFAULT_SPACE_NAME", "Nova")
    monkeypatch.setattr(space_engine, "DEFAULT_SPACE_ALIASES", {"novaspace"})
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)

    space = space_engine.get_or_create_space("novaspace", "NovaSpace")

    assert space.slug == "nova"
    assert (spaces_root / "nova" / "space.yaml").exists()
    assert not (spaces_root / "novaspace").exists()
