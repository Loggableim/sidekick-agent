from __future__ import annotations

import pytest


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


def test_space_governance_defaults_off_and_malformed_records_fail_closed(monkeypatch, tmp_path):
    """A broken or absent governance record must never enable supervision."""
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")

    space = space_engine.Space("alpha", "Alpha")
    assert space.load_config()["nova_management"] == {
        "yolo": False,
        "enrolled": False,
        "revision": 0,
    }
    assert space.load_config()["space_id"] == ""

    space.root.mkdir(parents=True)
    space.config_path.write_text(
        "nova_management:\n  yolo: 'true'\n  enrolled: true\n  revision: '7'\n",
        encoding="utf-8",
    )

    config = space.load_config()
    assert config["nova_management"] == {
        "yolo": False,
        "enrolled": False,
        "revision": 0,
    }


def test_space_governance_persists_identity_and_increments_revision(monkeypatch, tmp_path):
    """A legitimate write creates a durable identity and each governance write advances it."""
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")

    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha", "project_dir": str(project)})
    space_id = space.load_config()["space_id"]
    confirmation = {
        "space_id": space_id,
        "root_fingerprint": space_engine.space_root_fingerprint(project),
    }

    enrolled = space_engine.update_nova_management(
        space,
        yolo=True,
        enrolled=True,
        confirmation=confirmation,
        trusted_project_root=project,
        actor="dashboard:test",
    )
    disabled = space_engine.update_nova_management(
        space,
        yolo=False,
        enrolled=False,
        confirmation=None,
        trusted_project_root=project,
        actor="dashboard:test",
    )

    assert space.load_config()["space_id"] == space_id
    assert enrolled == {"yolo": True, "enrolled": True, "revision": 1}
    assert disabled == {"yolo": False, "enrolled": False, "revision": 2}


def test_space_governance_rejects_enrollment_without_a_trusted_project(monkeypatch, tmp_path):
    """Client confirmation alone cannot enroll a Space with no trusted project root."""
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")

    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha", "project_dir": str(project)})
    space_id = space.load_config()["space_id"]

    with pytest.raises(space_engine.SpaceGovernanceError):
        space_engine.update_nova_management(
            space,
            yolo=True,
            enrolled=True,
            confirmation={
                "space_id": space_id,
                "root_fingerprint": space_engine.space_root_fingerprint(project),
            },
            trusted_project_root=None,
            actor="dashboard:test",
        )


def test_space_governance_rolls_back_when_its_required_audit_append_fails(monkeypatch, tmp_path):
    """A management transition cannot persist when its append-only evidence fails."""
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha"})
    before = space.config_path.read_bytes()
    monkeypatch.setattr(
        space_engine,
        "_append_nova_management_audit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
        raising=False,
    )

    with pytest.raises(space_engine.SpaceGovernanceError):
        space_engine.update_nova_management(
            space,
            yolo=True,
            enrolled=False,
            confirmation=None,
            trusted_project_root=None,
            actor="dashboard:test",
        )

    assert space.config_path.read_bytes() == before
