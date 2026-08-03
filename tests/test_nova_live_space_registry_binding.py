"""Read-only regressions for live Space/project-root registry mismatches.

The WebUI Space registry and the project checkout are intentionally separate
authorities.  A copied ``space.yaml`` must not make an external checkout an
enrollment root merely because its slug matches an active Space.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_external_project_root_for_matching_slug_fails_closed_without_trust(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A matching slug cannot bridge an unregistered project root.

    This mirrors the live layout where the active Sidekick Space registry can
    live under one home while the checkout lives under a separate portable
    installation.  The check is read-only and leaves the persisted config
    untouched.
    """
    from nova.space_supervisor import resolve_managed_space_governance
    from web.api import space_engine, workspace

    spaces_root = tmp_path / "sidekick-home" / "spaces"
    active_space_root = spaces_root / "aquarium-zentrum"
    project_root = tmp_path / "hermes-portable" / "home" / "spaces" / "aquarium-zentrum"
    active_space_root.mkdir(parents=True)
    project_root.mkdir(parents=True)

    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy-spaces")
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_ROOTS", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)

    space = space_engine.Space("aquarium-zentrum", "Aquarium-Zentrum")
    space.save_config(
        {
            "name": "Aquarium-Zentrum",
            "project_dir": str(project_root),
            "space_id": "f06888c2505a40e3aeb5396d858a4c42",
            "nova_management": {"yolo": True, "enrolled": True, "revision": 2},
            "nova_management_audit": [],
        }
    )
    before = space.config_path.read_bytes()

    # Make the fixture's project root external to the active user's home and
    # remove all other independently trusted roots.  No filesystem mutation is
    # performed by the enrollment resolver.
    fake_home = tmp_path / "unrelated-user-home"
    fake_home.mkdir()
    monkeypatch.setattr(workspace.Path, "home", classmethod(lambda _cls: fake_home))
    monkeypatch.setattr(workspace, "_read_only_profile_context", lambda: None)
    monkeypatch.setattr(workspace, "_read_only_saved_workspace_paths", lambda *_a: set())
    monkeypatch.setattr(workspace, "_read_only_default_workspaces", lambda: ())

    with pytest.raises(ValueError, match="independently trusted workspace root"):
        resolve_managed_space_governance("aquarium-zentrum")

    assert space.config_path.read_bytes() == before



def test_live_three_space_snapshot_keeps_nova_management_aquarium_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production roots expose one autonomous Space and no more.

    This is deliberately a read-only contract against the portable installation
    used for the real Nova/Aquarium setup. It is skipped on fresh installations
    where those roots do not exist yet; temporary-root API tests cover that path.
    """
    import hashlib
    import re

    live_sidekick_spaces = Path(r"C:\sidekick\home\spaces")
    live_project_spaces = Path(r"C:\HermesPortable\home\spaces")
    product_slugs = ("nova", "finanzjunkie", "aquarium-zentrum")
    configs = [live_sidekick_spaces / slug / "space.yaml" for slug in product_slugs]
    project_config = live_project_spaces / "aquarium-zentrum" / "space.yaml"
    if not all(path.is_file() for path in configs) or not project_config.is_file():
        pytest.skip("live portable Space roots are not provisioned")

    from nova.space_supervisor import resolve_managed_space_governance
    from web.api import nova_presence, space_engine

    monkeypatch.setattr(space_engine, "SPACES_ROOT", live_sidekick_spaces)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_ROOTS", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)

    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    before = {str(path): digest(path) for path in [*configs, project_config]}
    snapshots = {
        slug: nova_presence._read_space_config(live_sidekick_spaces / slug / "space.yaml")
        for slug in product_slugs
    }
    ids = [str(snapshots[slug].get("space_id") or "") for slug in product_slugs]
    assert all(re.fullmatch(r"[0-9a-f]{32}", value) for value in ids)
    assert len(set(ids)) == len(ids)

    for slug in ("nova", "finanzjunkie"):
        assert snapshots[slug]["nova_management"] == {
            "enrolled": False,
            "revision": 0,
            "yolo": False,
        }
    aquarium = snapshots["aquarium-zentrum"]
    assert aquarium["nova_management"] == {
        "enrolled": True,
        "revision": 2,
        "yolo": True,
    }
    aquarium_root = Path(str(aquarium["project_dir"])).resolve()
    assert aquarium_root == (live_project_spaces / "aquarium-zentrum").resolve()
    assert aquarium_root.is_dir()
    audit = aquarium["nova_management_audit"][-1]
    assert audit["space_id"] == aquarium["space_id"]
    assert audit["root_fingerprint"] == space_engine.space_root_fingerprint(aquarium_root)

    governance = resolve_managed_space_governance("aquarium-zentrum")
    assert governance.space_id == aquarium["space_id"]
    assert governance.canonical_root == aquarium_root
    assert governance.yolo is True and governance.enrolled is True
    assert governance.revision == aquarium["nova_management"]["revision"]
    for slug in ("nova", "finanzjunkie"):
        with pytest.raises(ValueError):
            resolve_managed_space_governance(slug)

    card = nova_presence.build_presence_card(home=live_sidekick_spaces.parent)
    assert [item["space"] for item in card["managed_spaces"]] == ["aquarium-zentrum"]
    assert {item["space"] for item in card["managed_spaces"]}.isdisjoint(
        {"nova", "finanzjunkie"}
    )
    after = {str(path): digest(path) for path in [*configs, project_config]}
    assert before == after