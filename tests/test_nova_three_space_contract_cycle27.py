"""Integration contract for the three fixed Nova Spaces.

The regression combines the read-only WebUI management/presence surface with
live-test readiness diagnostics.  It deliberately supplies no readiness
callbacks, so an audit can never reach a listener, provider, or verifier.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from nova.live_test_gate import audit_three_space_readiness

from test_fastapi_route_bridge import DASHBOARD_ACTOR, _headers


def test_three_space_api_and_readiness_contract_stays_read_only(monkeypatch, tmp_path: Path) -> None:
    from cli import web_server
    from web.api import space_engine, workspace

    spaces_root = tmp_path / "spaces"
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy-spaces")
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_ROOTS", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)

    project_roots: dict[str, Path] = {}
    spaces: dict[str, object] = {}
    for slug in ("nova", "finanzjunkie", "aquarium-zentrum"):
        root = tmp_path / "projects" / slug
        root.mkdir(parents=True)
        project_roots[slug] = root
        space = space_engine.Space(slug, slug)
        space.save_config({"name": slug, "project_dir": str(root)}, mint_space_id=True)
        spaces[slug] = space

    aquarium = spaces["aquarium-zentrum"]
    config = aquarium.load_config()
    space_engine.update_nova_management(
        aquarium,
        yolo=True,
        enrolled=True,
        confirmation={
            "space_id": config["space_id"],
            "root_fingerprint": space_engine.space_root_fingerprint(project_roots["aquarium-zentrum"]),
        },
        trusted_project_root=project_roots["aquarium-zentrum"],
        actor=DASHBOARD_ACTOR,
    )

    # Read-only route resolution may inspect persisted roots but must not use a
    # mutating trusted-workspace resolver or start the supervision ticker.
    monkeypatch.setattr(web_server, "resolve_enrollment_trusted_workspace_read_only", lambda value: Path(value))
    monkeypatch.setattr(workspace, "resolve_enrollment_trusted_workspace_read_only", lambda value: Path(value))
    monkeypatch.setenv("SIDEKICK_HOME", str(tmp_path))
    monkeypatch.setattr(
        web_server,
        "_start_nova_space_supervision_ticker",
        lambda: (_ for _ in ()).throw(AssertionError("GET must not start Nova")),
    )

    fingerprint = hashlib.sha256(str(project_roots["aquarium-zentrum"].resolve()).encode("utf-8")).hexdigest()
    audit = audit_three_space_readiness(
        project_roots,
        environ={
            "SIDEKICK_NOVA_LIVE_TEST_SPACE": "aquarium-zentrum",
            "SIDEKICK_NOVA_LIVE_TEST_ENABLED": "1",
            "SIDEKICK_NOVA_LIVE_TEST_SPACE_ID": config["space_id"],
            "SIDEKICK_NOVA_LIVE_TEST_ROOT": str(project_roots["aquarium-zentrum"]),
            "SIDEKICK_NOVA_LIVE_TEST_ROOT_FINGERPRINT": fingerprint,
        },
    )
    by_space = {item["target_space"]: item for item in audit["spaces"]}
    assert audit["read_only"] is True and audit["ready"] is False
    assert by_space["nova"]["reason"] == "test_space_not_authorized"
    assert by_space["finanz-junkie"]["reason"] == "test_space_not_authorized"
    assert by_space["aquarium-zentrum"]["reason"] == "readiness_contract_missing"
    assert all(item["allowed"] is False for item in audit["spaces"])
    assert "SIDEKICK_NOVA_LIVE_TEST_ROOT" not in json.dumps(audit)

    before = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    client = TestClient(web_server.app)
    headers = _headers(web_server)
    management = {
        slug: client.get(f"/api/space/nova-management?slug={slug}", headers=headers)
        for slug in spaces
    }
    assert all(response.status_code == 200 for response in management.values())
    assert management["aquarium-zentrum"].json()["nova_management"]["enrolled"] is True
    assert management["nova"].json()["nova_management"]["enrolled"] is False
    assert management["finanzjunkie"].json()["nova_management"]["enrolled"] is False

    presence = client.get("/api/nova/presence-card", headers=headers)
    assert presence.status_code == 200
    payload = presence.json()
    assert [item["space"] for item in payload["managed_spaces"]] == ["aquarium-zentrum"]
    assert json.dumps(payload["managed_spaces"]).lower().count("nova") == 0

    after = {
        str(path.relative_to(tmp_path)): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert before == after
