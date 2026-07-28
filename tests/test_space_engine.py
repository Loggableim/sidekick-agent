from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import uuid

import pytest


def _audit_event(space_id: str, *, revision: int, previous: dict, next_record: dict) -> dict:
    return {
        "actor": "dashboard:test",
        "timestamp": 1_700_000_000.0 + revision,
        "space_id": space_id,
        "root_fingerprint": "",
        "policy_revision": revision,
        "governance_revision": revision,
        "previous": previous,
        "next": next_record,
    }


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
    assert space.load_config()["space_id"]
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
    with pytest.raises(space_engine.SpaceGovernanceError, match="management record is malformed"):
        space_engine.update_nova_management(
            space,
            yolo=False,
            enrolled=False,
            confirmation=None,
            trusted_project_root=None,
            actor="dashboard:test",
        )


def test_space_governance_persists_identity_and_increments_revision(monkeypatch, tmp_path):
    """A legitimate write creates a durable identity and each governance write advances it."""
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")

    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha", "project_dir": str(project)}, mint_space_id=True)
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


def test_space_governance_rolls_back_when_atomic_management_write_fails(monkeypatch, tmp_path):
    """A management transition cannot persist when its atomic write fails."""
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha"})
    before = space.config_path.read_bytes()
    monkeypatch.setattr(
        space_engine.Space,
        "_atomic_write_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
        raising=False,
    )

    with pytest.raises(OSError):
        space_engine.update_nova_management(
            space,
            yolo=True,
            enrolled=False,
            confirmation=None,
            trusted_project_root=None,
            actor="dashboard:test",
        )

    assert space.config_path.read_bytes() == before


def test_management_rejects_a_malformed_audit_without_overwriting_it(monkeypatch, tmp_path):
    """Malformed audit evidence fails closed instead of being silently replaced."""
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.root.mkdir(parents=True)
    space.config_path.write_text(
        "name: Alpha\nnova_management_audit: malformed\n",
        encoding="utf-8",
    )
    before = space.config_path.read_bytes()

    with pytest.raises(space_engine.SpaceGovernanceError, match="audit is malformed"):
        space_engine.update_nova_management(
            space,
            yolo=True,
            enrolled=False,
            confirmation=None,
            trusted_project_root=None,
            actor="dashboard:test",
        )

    assert space.config_path.read_bytes() == before


def test_management_persists_governance_and_audit_together_in_space_config(monkeypatch, tmp_path):
    """The audit history is atomically bound to the same config revision."""
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha"}, mint_space_id=True)

    space_engine.update_nova_management(
        space,
        yolo=True,
        enrolled=False,
        confirmation=None,
        trusted_project_root=None,
        actor="dashboard:test",
    )

    config = space.load_config()
    assert config["nova_management"]["revision"] == 1
    assert config["nova_management_audit"][-1]["next"]["revision"] == 1
    assert space_engine.list_nova_management_audit(space) == config["nova_management_audit"]
    assert not (space.root / "nova-management-audit.jsonl").exists()


def test_management_save_failure_leaves_config_and_audit_unchanged(monkeypatch, tmp_path):
    """A failed atomic write cannot expose a new governance state without its audit."""
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha"}, mint_space_id=True)
    before = space.config_path.read_bytes()
    monkeypatch.setattr(
        space_engine.Space,
        "_atomic_write_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
        raising=False,
    )

    with pytest.raises(OSError):
        space_engine.update_nova_management(
            space,
            yolo=True,
            enrolled=False,
            confirmation=None,
            trusted_project_root=None,
            actor="dashboard:test",
        )

    assert space.config_path.read_bytes() == before
    assert space.load_config()["nova_management_audit"] == []


def test_concurrent_management_updates_form_one_strict_revision_chain(monkeypatch, tmp_path):
    """Concurrent writes serialize into one coherent audit and revision sequence."""
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha"}, mint_space_id=True)

    def transition(enabled):
        return space_engine.update_nova_management(
            space,
            yolo=enabled,
            enrolled=False,
            confirmation=None,
            trusted_project_root=None,
            actor="dashboard:test",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(transition, [True, False] * 8))

    audit = space_engine.list_nova_management_audit(space)
    assert sorted(item["revision"] for item in results) == list(range(1, 17))
    assert [event["governance_revision"] for event in audit] == list(range(1, 17))
    assert all(event["previous"]["revision"] + 1 == event["next"]["revision"] for event in audit)
    assert (space.root / ".space-config.lock").is_file()


def test_audit_read_returns_valid_legacy_jsonl_without_migrating(monkeypatch, tmp_path):
    """Pure audit reads retain existing JSONL evidence without rewriting YAML."""
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space_id = uuid.uuid4().hex
    space.save_config({
        "name": "Alpha",
        "space_id": space_id,
        "nova_management": {"yolo": True, "enrolled": False, "revision": 1},
    })
    before = space.config_path.read_bytes()
    event = _audit_event(
        space_id,
        revision=1,
        previous={"yolo": False, "enrolled": False, "revision": 0},
        next_record={"yolo": True, "enrolled": False, "revision": 1},
    )
    (space.root / "nova-management-audit.jsonl").write_text(
        json.dumps(event) + "\n", encoding="utf-8"
    )

    assert space_engine.list_nova_management_audit(space) == [event]
    assert space.config_path.read_bytes() == before


def test_management_migrates_valid_legacy_audit_before_appending(monkeypatch, tmp_path):
    """The first YAML management write carries JSONL history into its transaction."""
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space_id = uuid.uuid4().hex
    space.save_config({
        "name": "Alpha",
        "space_id": space_id,
        "nova_management": {"yolo": True, "enrolled": False, "revision": 1},
    })
    legacy = _audit_event(
        space_id,
        revision=1,
        previous={"yolo": False, "enrolled": False, "revision": 0},
        next_record={"yolo": True, "enrolled": False, "revision": 1},
    )
    (space.root / "nova-management-audit.jsonl").write_text(
        json.dumps(legacy) + "\n", encoding="utf-8"
    )

    result = space_engine.update_nova_management(
        space,
        yolo=False,
        enrolled=False,
        confirmation=None,
        trusted_project_root=None,
        actor="dashboard:test",
    )

    assert result["revision"] == 2
    assert space.load_config()["nova_management_audit"] == space_engine.list_nova_management_audit(space)
    assert len(space_engine.list_nova_management_audit(space)) == 2


def test_management_rejects_malformed_audit_list_item(monkeypatch, tmp_path):
    """A non-event YAML list entry is evidence corruption, not an ignorable row."""
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.root.mkdir(parents=True)
    space.config_path.write_text(
        f"space_id: {uuid.uuid4().hex}\nnova_management_audit:\n- broken\n",
        encoding="utf-8",
    )

    with pytest.raises(space_engine.SpaceGovernanceError, match="audit"):
        space_engine.update_nova_management(
            space, yolo=True, enrolled=False, confirmation=None,
            trusted_project_root=None, actor="dashboard:test",
        )
    with pytest.raises(space_engine.SpaceGovernanceError, match="audit"):
        space_engine.list_nova_management_audit(space)


def test_management_rejects_broken_audit_revision_chain(monkeypatch, tmp_path):
    """Audit events must form one exact previous/next revision chain."""
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space_id = uuid.uuid4().hex
    first = _audit_event(
        space_id, revision=1,
        previous={"yolo": False, "enrolled": False, "revision": 0},
        next_record={"yolo": True, "enrolled": False, "revision": 1},
    )
    second = _audit_event(
        space_id, revision=3,
        previous={"yolo": False, "enrolled": False, "revision": 2},
        next_record={"yolo": False, "enrolled": False, "revision": 3},
    )
    space.root.mkdir(parents=True)
    space.config_path.write_text(
        "space_id: " + space_id + "\nnova_management_audit:\n" +
        "- " + json.dumps(first) + "\n- " + json.dumps(second) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(space_engine.SpaceGovernanceError, match="audit"):
        space_engine.update_nova_management(
            space, yolo=False, enrolled=False, confirmation=None,
            trusted_project_root=None, actor="dashboard:test",
        )


def test_shared_config_lock_prevents_stale_generic_overwrite(monkeypatch, tmp_path):
    """A generic edit reloads under the governance lock and retains a concurrent audit."""
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "workspaces")
    space = space_engine.Space("alpha", "Alpha")
    space.save_config({"name": "Alpha"}, mint_space_id=True)
    generic_loaded = threading.Event()
    release_generic = threading.Event()
    original_load = space.load_config

    def delayed_load():
        if threading.current_thread().name == "generic":
            generic_loaded.set()
            assert release_generic.wait(timeout=5)
        return original_load()

    monkeypatch.setattr(space, "load_config", delayed_load)
    generic = threading.Thread(
        name="generic",
        target=lambda: space_engine.update_space_config(space, {"description": "kept"}),
    )
    generic.start()
    assert generic_loaded.wait(timeout=5)
    management = threading.Thread(
        target=lambda: space_engine.update_nova_management(
            space, yolo=True, enrolled=False, confirmation=None,
            trusted_project_root=None, actor="dashboard:test",
        ),
    )
    management.start()
    release_generic.set()
    generic.join(timeout=5)
    management.join(timeout=5)
    assert not generic.is_alive()
    assert not management.is_alive()

    config = original_load()
    assert config["description"] == "kept"
    assert config["nova_management"]["revision"] == 1
    assert [event["governance_revision"] for event in config["nova_management_audit"]] == [1]
