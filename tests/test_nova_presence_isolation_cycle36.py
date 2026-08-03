"""Presence payload shape and managed-Space isolation regressions."""

from __future__ import annotations

import json
from pathlib import Path


def test_presence_refetch_always_has_space_bound_managed_spaces(tmp_path: Path, monkeypatch) -> None:
    from web.api import nova_presence, space_engine

    home = tmp_path / "home"; spaces_root = home / "spaces"
    for slug in ("nova", "finanzjunkie", "aquarium-zentrum"):
        root = spaces_root / slug; root.mkdir(parents=True)
        root.joinpath("space.yaml").write_text(json.dumps({"name": slug, "project_dir": str(root), "space_id": ("a" * 32), "nova_management": {"yolo": slug == "aquarium-zentrum", "enrolled": slug == "aquarium-zentrum", "revision": 1 if slug == "aquarium-zentrum" else 0}}), encoding="utf-8")
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(nova_presence, "_managed_space_summaries", lambda _root: [{"space": "aquarium-zentrum", "state": "idle"}])
    monkeypatch.setattr("web.api.workspace.resolve_enrollment_trusted_workspace_read_only", lambda value: Path(value))
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_ROOTS", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)

    first = nova_presence.build_presence_card(home=home)
    assert "managed_spaces" in first
    assert [item["space"] for item in first["managed_spaces"]] == ["aquarium-zentrum"]
    assert all(slug not in json.dumps(first["managed_spaces"]).lower() for slug in ("nova", "finanzjunkie"))

    # A stale refetch after revocation must return an explicit empty list, not
    # omit the field and leave the UI's previous Aquarium card visible.
    aquarium_config = spaces_root / "aquarium-zentrum" / "space.yaml"
    config = json.loads(aquarium_config.read_text(encoding="utf-8"))
    config["nova_management"] = {"yolo": False, "enrolled": False, "revision": 2}
    aquarium_config.write_text(json.dumps(config), encoding="utf-8")
    monkeypatch.setattr(nova_presence, "_managed_space_summaries", lambda _root: [])
    second = nova_presence.build_presence_card(home=home)
    assert "managed_spaces" in second and second["managed_spaces"] == []








