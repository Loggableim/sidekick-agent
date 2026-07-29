"""Regression coverage for Nova's public, read-only presence projection."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import pytest
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


def _write_marker_space(path: Path, *, slug: str, revision: int = 7) -> tuple[str, str]:
    """Write the smallest audited binding a marker reader may trust."""
    space_id = uuid4().hex
    root_fingerprint = "a" * 64
    management = {"yolo": True, "enrolled": True, "revision": revision}
    _write_json(
        path / "space.yaml",
        {
            "name": slug.title(),
            "project_dir": "C:/private/project-root",
            "space_id": space_id,
            "nova_management": management,
            "nova_management_audit": [
                {
                    "actor": "dashboard:" + "b" * 64,
                    "timestamp": 1.0,
                    "space_id": space_id,
                    "root_fingerprint": root_fingerprint,
                    "policy_revision": revision,
                    "governance_revision": revision,
                    "previous": {"yolo": False, "enrolled": False, "revision": revision - 1},
                    "next": management,
                }
            ],
        },
    )
    return space_id, root_fingerprint


def _write_scheduler_marker_state(
    path: Path,
    *,
    space: str,
    space_id: str,
    root_fingerprint: str,
    revision: int,
    pending_digest: str = "",
    current_reference_digest: str = "c" * 64,
    last_evaluated_reference_digest: str = "e" * 64,
    last_checked_at: object = None,
    last_check_code: object = "",
) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS nova_supervision_space_state (
                target_key TEXT PRIMARY KEY,
                target_space_id TEXT NOT NULL,
                root_fingerprint TEXT NOT NULL,
                pending_digest TEXT NOT NULL,
                pending_reason_code TEXT NOT NULL,
                pending_count INTEGER NOT NULL,
                governance_revision INTEGER NOT NULL,
                last_started_at REAL,
                current_reference_digest TEXT NOT NULL,
                last_evaluated_reference_digest TEXT NOT NULL,
                last_checked_at REAL,
                last_check_code TEXT NOT NULL,
                last_outcome_code TEXT NOT NULL,
                updated_at REAL NOT NULL
            )"""
        )
        connection.execute(
            """INSERT OR REPLACE INTO nova_supervision_space_state (
                target_key, target_space_id, root_fingerprint,
                pending_digest, pending_reason_code, pending_count,
                governance_revision, last_started_at,
                current_reference_digest, last_evaluated_reference_digest,
                last_checked_at, last_check_code, last_outcome_code, updated_at
            ) VALUES (?, ?, ?, ?, 'ci_change', 0, ?, NULL, ?, ?, ?, ?, '', 1.0)""",
            (
                space,
                space_id,
                root_fingerprint,
                pending_digest,
                revision,
                current_reference_digest,
                last_evaluated_reference_digest,
                last_checked_at,
                last_check_code,
            ),
        )


def _write_ledger(path: Path) -> None:
    """Create the exact normal supervisor schema before seeding fixture data."""
    from nova.space_supervisor import ManagedSpaceSupervisor

    path.parent.mkdir(parents=True, exist_ok=True)
    ManagedSpaceSupervisor(
        ledger_path=path,
        governance_resolver=lambda _target: None,
    ).start()
    with sqlite3.connect(path) as connection:
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


def _insert_admission(
    connection: sqlite3.Connection,
    *,
    admission_id: str,
    space: str,
    state: str,
    updated_at: str,
) -> None:
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
            admission_id,
            space,
            f"space-{space}",
            f"intent-{admission_id}",
            f"C:/private/{space}",
            f"fingerprint-{space}",
            1,
            "policy",
            "[]",
            "contract",
            f"run-{admission_id}",
            state,
            1,
            1,
            "2025-01-01T00:00:00+00:00",
            updated_at,
            None,
        ),
    )


def _insert_audit(
    connection: sqlite3.Connection,
    *,
    admission_id: str,
    event_type: str,
    created_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO supervisor_audit (
            admission_id, event_type, actor, reason, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (admission_id, event_type, "test:actor", "private detail", created_at),
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
    assert payload["change_markers"] == []
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


def test_presence_card_projects_only_redacted_scheduler_marker_codes(tmp_path):
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    space_id, root_fingerprint = _write_marker_space(
        home / "spaces" / "alpha", slug="alpha"
    )
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)
    _write_scheduler_marker_state(
        ledger,
        space="alpha",
        space_id=space_id,
        root_fingerprint=root_fingerprint,
        revision=7,
        pending_digest="d" * 64,
        current_reference_digest="commit-secret-".ljust(64, "c"),
        last_evaluated_reference_digest="e" * 64,
    )

    payload = build_presence_card(home=home)

    assert payload["change_markers"] == [
        {"space": "alpha", "state_code": "change_detected", "checked_at": None}
    ]
    rendered = json.dumps(payload)
    assert "digest" not in rendered
    assert "commit-secret" not in rendered


def test_presence_card_projects_valid_unchanged_marker_with_utc_checkpoint(tmp_path):
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    space_id, root_fingerprint = _write_marker_space(
        home / "spaces" / "alpha", slug="alpha"
    )
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)
    _write_scheduler_marker_state(
        ledger,
        space="alpha",
        space_id=space_id,
        root_fingerprint=root_fingerprint,
        revision=7,
        current_reference_digest="d" * 64,
        last_evaluated_reference_digest="d" * 64,
        last_checked_at=1.0,
        last_check_code="unchanged",
    )

    payload = build_presence_card(home=home)

    assert payload["change_markers"] == [
        {
            "space": "alpha",
            "state_code": "reference_unchanged",
            "checked_at": "1970-01-01T00:00:01+00:00",
        }
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("root_fingerprint", "f" * 64),
        ("revision", 8),
        ("current_reference_digest", "invalid"),
        ("last_check_code", "model_output"),
        ("last_checked_at", float("nan")),
        ("last_checked_at", 0.0),
    ),
)
def test_presence_card_omits_invalid_or_mismatched_scheduler_marker_only(
    tmp_path, field, value
):
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    space_id, root_fingerprint = _write_marker_space(
        home / "spaces" / "alpha", slug="alpha"
    )
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)
    values: dict[str, object] = {
        "space": "alpha",
        "space_id": space_id,
        "root_fingerprint": root_fingerprint,
        "revision": 7,
        "current_reference_digest": "d" * 64,
        "last_evaluated_reference_digest": "d" * 64,
        "last_checked_at": 1.0,
        "last_check_code": "unchanged",
    }
    values[field] = value
    _write_scheduler_marker_state(ledger, **values)

    payload = build_presence_card(home=home)

    assert payload["managed_spaces"] == [
        {"space": "alpha", "name": "Alpha", "governance_revision": 7, "state": "paused"}
    ]
    assert payload["change_markers"] == []


def test_presence_card_omits_marker_from_partial_or_wal_backed_scheduler_state(tmp_path):
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    space_id, root_fingerprint = _write_marker_space(
        home / "spaces" / "alpha", slug="alpha"
    )
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)
    _write_scheduler_marker_state(
        ledger,
        space="alpha",
        space_id=space_id,
        root_fingerprint=root_fingerprint,
        revision=7,
        current_reference_digest="d" * 64,
        last_evaluated_reference_digest="d" * 64,
        last_checked_at=1.0,
        last_check_code="unchanged",
    )
    with sqlite3.connect(ledger) as connection:
        connection.execute("DROP TABLE nova_supervision_space_state")
        connection.execute(
            """CREATE TABLE nova_supervision_space_state (
                target_key TEXT PRIMARY KEY, current_reference_digest TEXT NOT NULL
            )"""
        )
    before_partial = _tree_snapshot(home)

    assert build_presence_card(home=home)["change_markers"] == []
    assert _tree_snapshot(home) == before_partial

    with sqlite3.connect(ledger) as connection:
        connection.execute("DROP TABLE nova_supervision_space_state")
    wal_path = ledger.with_name(ledger.name + "-wal")
    wal_path.write_bytes(b"untrusted-wal-snapshot")
    before_wal = _tree_snapshot(home)

    assert build_presence_card(home=home)["change_markers"] == []
    assert _tree_snapshot(home) == before_wal


def test_presence_card_does_not_create_missing_runtime_state(tmp_path):
    """Catches a GET path calling default EntityStateStore or a migration helper."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "missing-home"

    assert build_presence_card(home=home)["managed_spaces"] == []
    assert not home.exists()


def test_presence_card_fails_closed_when_legacy_ledger_lacks_read_indexes(tmp_path):
    """A page read must never fall back to scanning an unmigrated history."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    _write_space(home / "spaces" / "alpha", slug="alpha", name="Alpha", revision=7)
    ledger_path = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger_path)
    with sqlite3.connect(ledger_path) as connection:
        connection.execute("DROP INDEX idx_supervisor_admissions_target_updated")
        connection.execute("DROP INDEX idx_supervisor_audit_admission_sequence")
    before = _tree_snapshot(home)

    payload = build_presence_card(home=home)

    assert payload["managed_spaces"][0]["state"] == "idle"
    assert payload["activity"] == []
    assert payload["audited_results"] == []
    assert _tree_snapshot(home) == before


def test_presence_card_uses_latest_supervisor_focus_and_keeps_audited_results(tmp_path):
    """Unmanaged history must not starve managed focus, feed, or results."""
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
                "admission-ghost",
                "ghost",
                "space-ghost",
                "intent-ghost",
                "C:/private/ghost",
                "fingerprint-ghost",
                1,
                "policy",
                "[]",
                "contract",
                "run-ghost-secret",
                "completed",
                1,
                1,
                "2026-07-29T10:02:00+00:00",
                "2026-07-29T12:00:00+00:00",
                None,
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
                    "admission-ghost",
                    "completed",
                    "dashboard:actor",
                    "no public detail",
                    f"2026-07-29T11:{index:02d}:00+00:00",
                ),
            )

    payload = build_presence_card(home=home)

    assert payload["focus"] == {"kind": "supervision", "space": "beta", "state": "completed"}
    assert payload["activity"] == [
        {"kind": "reconciled_completed", "space": "beta", "at": "2026-07-29T10:06:00+00:00"},
        {"kind": "paused", "space": "alpha", "at": "2026-07-29T10:05:00+00:00"}
    ]
    assert payload["audited_results"] == [
        {"space": "beta", "result": "completed", "at": "2026-07-29T10:06:00+00:00"}
    ]
    assert "raw terminal detail" not in json.dumps(payload)


def test_presence_card_keeps_last_audited_result_while_newer_run_is_paused(tmp_path):
    """Current activity and the last terminal result are separate projections."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    _write_space(home / "spaces" / "alpha", slug="alpha", name="Alpha", revision=7)
    ledger_path = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger_path)
    with sqlite3.connect(ledger_path) as connection:
        _insert_admission(
            connection,
            admission_id="admission-alpha-completed",
            space="alpha",
            state="completed",
            updated_at="2026-07-28T10:00:00+00:00",
        )
        _insert_audit(
            connection,
            admission_id="admission-alpha-completed",
            event_type="completed",
            created_at="2026-07-28T10:00:00+00:00",
        )

    payload = build_presence_card(home=home)

    assert payload["activity"] == [
        {"kind": "paused", "space": "alpha", "at": "2026-07-29T10:05:00+00:00"}
    ]
    assert payload["audited_results"] == [
        {"space": "alpha", "result": "completed", "at": "2026-07-28T10:00:00+00:00"}
    ]


def test_presence_card_keeps_each_managed_space_visible_over_large_history(tmp_path):
    """One busy Space cannot consume the public activity/result read budget."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    _write_space(home / "spaces" / "alpha", slug="alpha", name="Alpha", revision=3)
    _write_space(home / "spaces" / "beta", slug="beta", name="Beta", revision=4)
    ledger_path = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger_path)
    with sqlite3.connect(ledger_path) as connection:
        for index in range(512):
            admission_id = f"old-alpha-{index:04d}"
            _insert_admission(
                connection,
                admission_id=admission_id,
                space="alpha",
                state="completed",
                updated_at=f"2025-01-{(index % 28) + 1:02d}T00:00:00+00:00",
            )
            _insert_audit(
                connection,
                admission_id=admission_id,
                event_type="completed",
                created_at=f"2025-01-{(index % 28) + 1:02d}T00:00:00+00:00",
            )
        _insert_admission(
            connection,
            admission_id="admission-beta-current",
            space="beta",
            state="completed",
            updated_at="2026-07-29T10:06:00+00:00",
        )
        _insert_audit(
            connection,
            admission_id="admission-beta-current",
            event_type="completed",
            created_at="2026-07-29T10:06:00+00:00",
        )
        for index in range(128):
            _insert_audit(
                connection,
                admission_id="admission-secret-id",
                event_type="completed",
                created_at=(
                    f"2026-07-29T11:{index // 60:02d}:{index % 60:02d}+00:00"
                ),
            )

    payload = build_presence_card(home=home)

    assert {entry["space"] for entry in payload["activity"]} == {"alpha", "beta"}
    assert {entry["space"] for entry in payload["audited_results"]} == {"alpha", "beta"}


def test_presence_admissions_query_is_one_indexed_lookup_per_managed_space(monkeypatch, tmp_path):
    """The presence read must seek one latest admission per managed Space."""
    import web.api.nova_presence as presence

    calls: list[tuple[str, tuple[object, ...]]] = []

    class _ReadOnlyLedger:
        def execute(self, query, params):
            calls.append((str(query), tuple(params)))
            return self

        def fetchall(self):
            return []

        def fetchone(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(presence, "_open_read_only_ledger", lambda _path: _ReadOnlyLedger())

    assert presence._read_supervisor_admissions(
        tmp_path / "ledger.sqlite",
        [{"space": "alpha"}, {"space": "beta"}],
    ) == []
    assert len(calls) == 2
    assert [params for _, params in calls] == [("alpha",), ("beta",)]
    for query, _params in calls:
        normalized_query = " ".join(query.split())
        assert "WHERE target_key = ?" in normalized_query
        assert "ORDER BY updated_at DESC" in normalized_query
        assert "LIMIT 1" in normalized_query

    calls.clear()
    assert presence._read_latest_terminal_events(
        tmp_path / "ledger.sqlite",
        [{"space": "alpha"}, {"space": "beta"}],
    ) == []
    assert len(calls) == 2
    assert [params for _, params in calls] == [
        ("alpha", "completed", "cancelled", "abandoned"),
        ("beta", "completed", "cancelled", "abandoned"),
    ]
    for query, _params in calls:
        normalized_query = " ".join(query.split())
        assert "WHERE target_key = ?" in normalized_query
        assert "state IN (?, ?, ?)" in normalized_query
        assert "ORDER BY updated_at DESC" in normalized_query
        assert "LIMIT 1" in normalized_query


def test_presence_latest_queries_use_supervisor_history_indexes(tmp_path):
    """The bounded per-Space lookups use normal-initializer indexes, never GET DDL."""
    import web.api.nova_presence as presence

    ledger_path = tmp_path / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger_path)
    with sqlite3.connect(ledger_path) as connection:
        connection.execute("PRAGMA automatic_index = OFF")
        admission_plan = connection.execute(
            f"EXPLAIN QUERY PLAN {presence._LATEST_ADMISSION_SQL}",
            ("alpha",),
        ).fetchall()
        audit_plan = connection.execute(
            f"EXPLAIN QUERY PLAN {presence._LATEST_AUDIT_SQL}",
            ("admission-secret-id",),
        ).fetchall()
        terminal_admission_plan = connection.execute(
            f"EXPLAIN QUERY PLAN {presence._LATEST_TERMINAL_ADMISSION_SQL}",
            ("alpha", *presence._TERMINAL_ADMISSION_STATES),
        ).fetchall()

    admission_details = "\n".join(str(row[3]) for row in admission_plan)
    audit_details = "\n".join(str(row[3]) for row in audit_plan)
    terminal_admission_details = "\n".join(str(row[3]) for row in terminal_admission_plan)
    assert "USING INDEX idx_supervisor_admissions_target_updated" in admission_details
    assert "USING INDEX idx_supervisor_audit_admission_sequence" in audit_details
    assert "USING INDEX idx_supervisor_admissions_target_updated" in terminal_admission_details
    assert "TEMP B-TREE" not in admission_details
    assert "TEMP B-TREE" not in audit_details
    assert "TEMP B-TREE" not in terminal_admission_details


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


def _forbid_mutating_nova_read_dependencies(monkeypatch) -> None:
    """Make a hidden lifecycle/store call fail loudly instead of writing test state."""
    import nova.presence as runtime_presence
    import web.api.nova_lifecycle as lifecycle

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Nova read endpoint invoked a mutating/runtime dependency")

    monkeypatch.setattr(lifecycle, "migration_tick", forbidden)
    monkeypatch.setattr(lifecycle, "repair_incomplete_events", forbidden)
    monkeypatch.setattr(lifecycle, "ensure_background_cron_jobs", forbidden)
    monkeypatch.setattr(lifecycle, "_run_local_script", forbidden)
    monkeypatch.setattr(lifecycle, "EntityKernel", forbidden)
    monkeypatch.setattr(lifecycle, "EntityStateStore", forbidden)
    monkeypatch.setattr(runtime_presence, "PresenceCoordinator", forbidden)


@pytest.mark.parametrize(
    ("path", "seeded"),
    [
        ("/api/nova/status", False),
        ("/api/nova/status", True),
        ("/api/nova/presence", False),
        ("/api/nova/presence", True),
    ],
)
def test_native_nova_status_reads_are_authenticated_and_side_effect_free(
    monkeypatch, tmp_path, path: str, seeded: bool,
):
    """Catches status/presence GETs bootstrapping Nova or probing models."""
    home = tmp_path / "home"
    if seeded:
        _write_json(
            home / "spaces" / "nova" / "nova_data" / "entity" / "entity_state.json",
            {
                "schema_version": 2,
                "revision": 9,
                "runtime": {"autonomy_level": 4, "last_event_id": "evt-public"},
                "dynamic": {
                    "presence": "thinking",
                    "voice_cycle": {"cycle_id": "cycle-public", "status": "thinking"},
                    "presence_updated_at": "2026-07-29T12:00:00+00:00",
                },
            },
        )
        _write_json(
            home / "spaces" / "nova" / ".lifecycle" / "reflection_queue.json",
            [{"status": "queued"}],
        )
    monkeypatch.setenv("SIDEKICK_HOME", str(home))

    from cli import web_server

    _forbid_mutating_nova_read_dependencies(monkeypatch)
    before = _tree_snapshot(home)
    client = TestClient(web_server.app, raise_server_exceptions=False)

    unauthorized = client.get(path)
    authorized = client.get(
        path,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )

    assert unauthorized.status_code == 401
    assert authorized.status_code == 200
    payload = authorized.json()
    if path == "/api/nova/status":
        assert payload["ok"] is True
        assert payload["autonomy_level"] == (4 if seeded else 2)
        assert isinstance(payload["models"], dict)
        assert payload["models"]["checked"] is False
    else:
        assert payload["presence"] == ("thinking" if seeded else "available")
        assert payload["voice_cycle"] == (
            {"cycle_id": "cycle-public", "status": "thinking"} if seeded else None
        )
    assert _tree_snapshot(home) == before


class _LegacyNovaReadHandler:
    headers = {"Host": "127.0.0.1"}
    client_address = ("127.0.0.1", 12345)

    def __init__(self) -> None:
        self.status_code: int | None = None
        self.response_headers: list[tuple[str, str]] = []
        self.rfile = io.BytesIO()
        self.wfile = io.BytesIO()

    def send_response(self, status: int, *_args) -> None:
        self.status_code = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers.append((name, value))

    def end_headers(self) -> None:
        return None


@pytest.mark.parametrize("path", ["/api/nova/status", "/api/nova/presence"])
def test_legacy_nova_status_reads_match_native_pure_projection(monkeypatch, tmp_path, path: str):
    """Catches the compatibility router falling back to mutating Nova helpers."""
    home = tmp_path / "home"
    _write_json(
        home / "spaces" / "nova" / "nova_data" / "entity" / "entity_state.json",
        {
            "runtime": {"autonomy_level": 3},
            "dynamic": {"presence": "listening", "voice_cycle": None},
        },
    )
    monkeypatch.setenv("SIDEKICK_HOME", str(home))

    from cli import web_server
    from web.api import routes

    _forbid_mutating_nova_read_dependencies(monkeypatch)
    before = _tree_snapshot(home)
    native = TestClient(web_server.app, raise_server_exceptions=False).get(
        path,
        headers={web_server._SESSION_HEADER_NAME: web_server._SESSION_TOKEN},
    )
    handler = _LegacyNovaReadHandler()
    handled = routes.handle_get(handler, urlparse(path))

    assert native.status_code == 200
    assert handled is None
    assert handler.status_code == 200
    assert json.loads(handler.wfile.getvalue().decode("utf-8")) == native.json()
    assert _tree_snapshot(home) == before
