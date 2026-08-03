import asyncio
import json
from pathlib import Path


def test_inventory_has_fixed_targets_and_missing_spaces_are_redacted(monkeypatch, tmp_path):
    from cli import web_server
    from web.api import space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", tmp_path / "spaces")
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy")
    payload = web_server._nova_space_inventory()

    assert payload["read_only"] is True
    assert payload["targets"] == ["nova", "finanz-junkie", "aquarium-zentrum"]
    assert [item["slug"] for item in payload["spaces"]] == payload["targets"]
    assert all(item["exists"] is False for item in payload["spaces"])
    assert all(item["next_step_codes"] == ["inspect_space_registry"] for item in payload["spaces"])
    assert "project_dir" not in json.dumps(payload)


def test_inventory_reports_verified_root_without_exposing_path(monkeypatch, tmp_path):
    from cli import web_server
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    project = tmp_path / "private-project"
    project.mkdir()
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy")
    space = space_engine.Space("aquarium-zentrum", "Aquarium")
    space.save_config({"name": "Aquarium", "project_dir": str(project)}, mint_space_id=True)
    monkeypatch.setattr(web_server, "resolve_enrollment_trusted_workspace_read_only", lambda value: project)

    item = web_server._redacted_nova_space_inventory_entry("aquarium-zentrum")

    assert item["exists"] is True
    assert item["identity"]["space_id_persisted"] is True
    assert item["root"]["status"] == "verified"
    assert item["root"]["fingerprint"] == space_engine.space_root_fingerprint(project)
    assert item["enrollment_readiness"]["next_step_code"] == "yolo_not_enabled" or item["next_step_codes"] == ["enable_space_yolo"]
    rendered = json.dumps(item)
    assert str(project) not in rendered
    assert "private-project" not in rendered


def test_inventory_endpoint_is_read_only_and_fixed(monkeypatch):
    from cli import web_server

    expected = {"spaces": [], "read_only": True, "targets": list(web_server._NOVA_INVENTORY_TARGETS)}
    monkeypatch.setattr(web_server, "_nova_space_inventory", lambda: expected)
    payload = asyncio.run(web_server.get_nova_space_inventory())
    assert payload == expected


def test_inventory_profile_home_isolated_and_reports_scope(monkeypatch, tmp_path):
    from cli import web_server
    from web.api import space_engine

    profile_a = tmp_path / "profile-a"
    profile_b = tmp_path / "profile-b"
    persisted_ids = {}
    for home, name in ((profile_a, "A"), (profile_b, "B")):
        root = home / "spaces" / "aquarium-zentrum"
        root.mkdir(parents=True)
        space = space_engine.Space("aquarium-zentrum", name, custom_root=home / "spaces")
        space.save_config({"name": name, "project_dir": ""}, mint_space_id=True)
        persisted_ids[name] = space.load_config()["space_id"]

    first = web_server._nova_space_inventory(profile_name="profile-a", profile_home=profile_a)
    second = web_server._nova_space_inventory(profile_name="profile-b", profile_home=profile_b)
    assert first["profile"] == {"scope": "profile-a"}
    assert second["profile"] == {"scope": "profile-b"}
    assert first["spaces"][2]["exists"] is True
    assert second["spaces"][2]["exists"] is True
    assert first["spaces"][2]["identity"]["space_id"] == persisted_ids["A"]
    assert second["spaces"][2]["identity"]["space_id"] == persisted_ids["B"]
    assert str(profile_a) not in json.dumps(first)
    assert str(profile_b) not in json.dumps(second)


def test_inventory_profile_root_cannot_escape_home(tmp_path):
    from cli import web_server
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "space.yaml").write_text("name: leaked", encoding="utf-8")
    item = web_server._profile_inventory_space("..", tmp_path / "profile")
    assert item is None


def test_three_space_readiness_audit_is_per_space_bounded_and_redacted(monkeypatch, tmp_path):
    """Presence inventory carries distinct safe reasons without activation or paths."""
    from cli import web_server
    from web.api import space_engine

    class FakeSpace:
        def __init__(self, slug: str):
            self.slug = slug
            self.config_path = tmp_path / (slug + ".yaml")

        def load_config(self):
            return {
                "name": self.slug,
                "space_id": (self.slug.replace("-", "") + "0" * 32)[:32],
                "project_dir": str(tmp_path / (self.slug + "-private-root")),
                "nova_management": {"yolo": False, "enrolled": False, "revision": 0},
            }

        def get_project_dir(self):
            return str(tmp_path / (self.slug + "-private-root"))

    reasons = {
        "nova": "space_id_required",
        "finanz-junkie": "project_dir_required",
        "aquarium-zentrum": "trusted_root_required",
    }
    monkeypatch.setattr(
        web_server, "_profile_inventory_space",
        lambda slug, _home: FakeSpace(slug),
    )
    monkeypatch.setattr(
        space_engine, "nova_enrollment_readiness",
        lambda space, trusted_project_root=None: {
            "state": "blocked", "ready": False, "space_id_persisted": True,
            "project_dir_configured": bool(space.get_project_dir()),
            "project_dir_available": False, "trusted_root_verified": False,
            "yolo": False, "enrolled": False, "governance_revision": 0,
            "reason_codes": [reasons[space.slug]], "next_step_code": "inspect_space_registry",
            "requires_explicit_write": False,
        },
    )
    monkeypatch.setattr(web_server, "_trusted_space_project_root", lambda *_a, **_k: None)

    payload = web_server._nova_space_inventory(profile_name="audit", profile_home=tmp_path)
    assert payload["read_only"] is True
    assert [item["slug"] for item in payload["spaces"]] == list(web_server._NOVA_INVENTORY_TARGETS)
    entries = {item["slug"]: item for item in payload["spaces"]}
    assert [entries[slug]["enrollment_readiness"]["reason_codes"] for slug in reasons] == [[code] for code in reasons.values()]
    rendered = json.dumps(payload)
    assert str(tmp_path) not in rendered
    assert "private-root" not in rendered
    assert all(item["enrollment_readiness"]["requires_explicit_write"] is False for item in payload["spaces"])