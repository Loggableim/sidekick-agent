"""Regression coverage for Nova's public, read-only presence projection."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_space(path: Path, *, slug: str, name: str, revision: int, enrolled: bool = True) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.joinpath("space.yaml").write_text(
        "\n".join(
            [
                f"name: {name}",
                "nova_management:",
                "  yolo: true",
                f"  enrolled: {'true' if enrolled else 'false'}",
                f"  revision: {revision}",
                "project_dir: C:/private/project-root",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_ledger(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE supervisor_admissions (
                admission_id TEXT PRIMARY KEY,
                target_key TEXT NOT NULL,
                target_space_id TEXT NOT NULL,
                intent_digest TEXT NOT NULL,
                canonical_root TEXT NOT NULL,
                root_fingerprint TEXT NOT NULL,
                governance_revision INTEGER NOT NULL,
                policy_identity TEXT NOT NULL,
                allowed_action_families_json TEXT NOT NULL,
                workflow_contract_digest TEXT NOT NULL,
                run_id TEXT NOT NULL,
                state TEXT NOT NULL,
                attachment_generation INTEGER NOT NULL,
                record_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                terminal_actor TEXT
            );
            CREATE TABLE supervisor_audit (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                admission_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor TEXT,
                reason TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO supervisor_admissions (
                admission_id, target_key, target_space_id, intent_digest,
                canonical_root, root_fingerprint, governance_revision,
                policy_identity, allowed_action_families_json,
                workflow_contract_digest, run_id, state,
                attachment_generation, record_version, created_at, updated_at,
                terminal_actor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "admission-secret-id",
                "alpha",
                "space-alpha",
                "intent-digest",
                "C:/private/project-root",
                "fingerprint",
                7,
                "policy",
                "[]",
                "contract",
                "run-secret-id",
                "paused",
                1,
                1,
                "2026-07-29T10:00:00+00:00",
                "2026-07-29T10:05:00+00:00",
                None,
            ),
        )
        connection.execute(
            """
            INSERT INTO supervisor_audit (
                admission_id, event_type, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "admission-secret-id",
                "paused",
                "dashboard:actor",
                "provider error api-key=super-secret-value",
                "2026-07-29T10:05:00+00:00",
            ),
        )


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    if not root.exists():
        return {}
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_presence_card_is_a_pure_redacted_projection(tmp_path):
    """Catches accidental store initialization or raw supervisor data leakage."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    _write_json(
        home / "spaces" / "nova" / "nova_data" / "entity" / "entity_state.json",
        {
            "identity": {
                "name": "Untrusted name with token super-secret-value",
                "description": "raw persona text must not become public API output",
            },
            "dynamic": {"presence": "thinking", "focus": 0.9},
        },
    )
    _write_space(home / "spaces" / "alpha", slug="alpha", name="Alpha", revision=7)
    _write_space(
        home / "spaces" / "not-enrolled",
        slug="not-enrolled",
        name="Not enrolled",
        revision=3,
        enrolled=False,
    )
    _write_ledger(home / "state" / "nova-space-supervisor.sqlite")
    before = _tree_snapshot(home)

    payload = build_presence_card(home=home)

    assert _tree_snapshot(home) == before
    assert payload["identity"] == {
        "name": "Nova",
        "voice": "direct, curious, accountable",
    }
    assert payload["state"] == "thinking"
    assert payload["focus"] == {"kind": "supervision", "space": "alpha", "state": "paused"}
    assert payload["managed_spaces"] == [
        {
            "space": "alpha",
            "name": "Alpha",
            "governance_revision": 7,
            "state": "paused",
        }
    ]
    assert payload["blockers"] == [{"space": "alpha", "code": "supervisor_paused"}]
    assert payload["activity"] == [
        {"kind": "paused", "space": "alpha", "at": "2026-07-29T10:05:00+00:00"}
    ]
    assert "C:/private/project-root" not in json.dumps(payload)
    assert "run-secret-id" not in json.dumps(payload)
    assert "super-secret-value" not in json.dumps(payload)
    assert "provider error" not in json.dumps(payload)
    assert "Untrusted name" not in json.dumps(payload)


def test_presence_card_does_not_create_missing_runtime_state(tmp_path):
    """Catches a GET path calling default EntityStateStore or a migration helper."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "missing-home"

    assert build_presence_card(home=home)["managed_spaces"] == []
    assert not home.exists()


def test_presence_card_uses_latest_supervisor_focus_and_keeps_audited_results(tmp_path):
    """A busy feed must not hide a prior audited completion or stale focus."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    _write_space(home / "spaces" / "alpha", slug="alpha", name="Alpha", revision=3)
    _write_space(home / "spaces" / "beta", slug="beta", name="Beta", revision=4)
    ledger_path = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger_path)
    with sqlite3.connect(ledger_path) as connection:
        connection.execute(
            """
            INSERT INTO supervisor_admissions (
                admission_id, target_key, target_space_id, intent_digest,
                canonical_root, root_fingerprint, governance_revision,
                policy_identity, allowed_action_families_json,
                workflow_contract_digest, run_id, state,
                attachment_generation, record_version, created_at, updated_at,
                terminal_actor
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "admission-beta",
                "beta",
                "space-beta",
                "intent-beta",
                "C:/private/beta",
                "fingerprint-beta",
                4,
                "policy",
                "[]",
                "contract",
                "run-beta-secret",
                "completed",
                1,
                1,
                "2026-07-29T10:01:00+00:00",
                "2026-07-29T10:06:00+00:00",
                None,
            ),
        )
        connection.execute(
            """
            INSERT INTO supervisor_audit (
                admission_id, event_type, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                "admission-beta",
                "reconciled_completed",
                "dashboard:actor",
                "raw terminal detail must stay private",
                "2026-07-29T10:06:00+00:00",
            ),
        )
        for index in range(60):
            connection.execute(
                """
                INSERT INTO supervisor_audit (
                    admission_id, event_type, actor, reason, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    "admission-secret-id",
                    "admitted",
                    "dashboard:actor",
                    "no public detail",
                    f"2026-07-29T11:{index:02d}:00+00:00",
                ),
            )

    payload = build_presence_card(home=home)

    assert payload["focus"] == {"kind": "supervision", "space": "beta", "state": "completed"}
    assert payload["audited_results"] == [
        {"space": "beta", "result": "completed", "at": "2026-07-29T10:06:00+00:00"}
    ]
    assert "raw terminal detail" not in json.dumps(payload)


def test_presence_card_is_native_authenticated_fastapi_read(monkeypatch, tmp_path):
    """Catches a presence read falling through to the mutating legacy bridge."""
    from cli import web_server

    home = tmp_path / "home"
    _write_json(
        home / "spaces" / "nova" / "nova_data" / "entity" / "entity_state.json",
        {"dynamic": {"presence": "available"}},
    )
    before = _tree_snapshot(home)
    monkeypatch.setenv("SIDEKICK_HOME", str(home))
    monkeypatch.setattr(
        web_server,
        "dispatch_route",
        lambda _request: (_ for _ in ()).throw(AssertionError("must not use route bridge")),
    )
    client = TestClient(web_server.app)

    unauthorized = client.get("/api/nova/presence-card")
    authorized = client.get(
        "/api/nova/presence-card",
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    assert authorized.json()["identity"]["name"] == "Nova"
    assert _tree_snapshot(home) == before
