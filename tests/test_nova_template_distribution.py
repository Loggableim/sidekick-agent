from __future__ import annotations

import importlib


def _reload_space_engine(monkeypatch, tmp_path):
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path))
    monkeypatch.setenv("SIDEKICK_WEBUI_SPACES_DIR", str(tmp_path / "spaces"))
    import web.api.space_engine as space_engine

    return importlib.reload(space_engine)


def test_seed_default_space_uses_bundled_nova_template_without_source(monkeypatch, tmp_path):
    space_engine = _reload_space_engine(monkeypatch, tmp_path)

    spaces = space_engine.get_all_spaces()
    nova = next(space for space in spaces if space.slug == "nova")

    assert nova.config_path.exists()
    assert (nova.root / "SOUL.md").exists()
    assert (nova.root / "AGENTS.md").exists()
    assert (nova.root / "emotion_config.json").exists()
    assert (nova.root / "emotion_mapper.py").exists()
    assert (nova.root / "emotion_decay.py").exists()
    assert (nova.root / "emotion_v2_bridge.py").exists()
    assert (nova.root / "memory").is_dir()
    assert nova.load_config()["nova"]["enabled"] is True
    assert "FISHAUDIO_API_KEY" not in (nova.root / "emotion_config.json").read_text("utf-8")


def test_bundled_template_does_not_overwrite_existing_space_files(monkeypatch, tmp_path):
    space_engine = _reload_space_engine(monkeypatch, tmp_path)
    nova = space_engine.Space("nova", "Nova")
    nova.root.mkdir(parents=True)
    soul = nova.root / "SOUL.md"
    soul.write_text("custom local soul", encoding="utf-8")
    existing = nova.root / "emotion_config.json"
    existing.write_text('{"custom": true}', encoding="utf-8")

    space_engine.ensure_bundled_nova_template(nova)

    assert soul.read_text("utf-8") == "custom local soul"
    assert existing.read_text("utf-8") == '{"custom": true}'
    assert (nova.root / "emotion_mapper.py").exists()


def test_template_manifest_contains_only_safe_relative_paths():
    from web.api.nova_template_distribution import BUNDLED_NOVA_TEMPLATE

    forbidden = {".env", "auth.json", "state.db", "sessions", "logs", "home", "spaces"}
    for rel in BUNDLED_NOVA_TEMPLATE:
        parts = set(rel.lower().replace("\\", "/").split("/"))
        assert not (parts & forbidden), rel
        assert ".." not in parts, rel
        assert not rel.startswith(("/", "\\")), rel

