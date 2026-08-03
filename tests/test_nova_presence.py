"""Regression coverage for Nova's public, read-only presence projection."""

from __future__ import annotations

import io
from hashlib import sha256
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
    # Presence now requires the same independently trusted root and chained
    # governance evidence as production enrollment.  Keep this legacy helper
    # aligned with that contract so tests cannot accidentally bless an
    # unbound `nova_management`` flag.
    _write_marker_space(path, slug=slug, revision=revision)
    config_path = path / "space.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["name"] = name
    if not enrolled:
        config["nova_management"] = {
            "yolo": True,
            "enrolled": False,
            "revision": revision,
        }
    config_path.write_text(json.dumps(config), encoding="utf-8")


def _write_marker_space(path: Path, *, slug: str, revision: int = 7) -> tuple[str, str]:
    """Write a fully chained audit and independently trusted project root."""
    space_id = uuid4().hex
    project_root = path / "trusted-project"
    project_root.mkdir(parents=True, exist_ok=True)
    root_fingerprint = sha256(str(project_root.resolve()).encode("utf-8")).hexdigest()
    management = {"yolo": True, "enrolled": True, "revision": revision}
    prior = {"yolo": False, "enrolled": False, "revision": 0}
    audit: list[dict[str, object]] = []
    for event_revision in range(1, revision + 1):
        following = {
            "yolo": event_revision == revision,
            "enrolled": event_revision == revision,
            "revision": event_revision,
        }
        audit.append(
            {
                "actor": "dashboard:" + "b" * 64,
                "timestamp": float(event_revision),
                "space_id": space_id,
                "root_fingerprint": root_fingerprint if event_revision == revision else "",
                "policy_revision": event_revision,
                "governance_revision": event_revision,
                "previous": prior,
                "next": following,
            }
        )
        prior = following
    _write_json(
        path / "space.yaml",
        {
            "name": slug.title(),
            "project_dir": str(project_root.resolve()),
            "space_id": space_id,
            "nova_management": management,
            "nova_management_audit": audit,
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
    pending_digest: object = "",
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
                pending_digest TEXT,
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


def test_presence_three_space_smoke_keeps_nova_and_unenrolled_spaces_out(
    tmp_path: Path,
):
    """Nova may expose only the independently enrolled Aquarium Space."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    _write_json(
        home / "spaces" / "nova" / "nova_data" / "entity" / "entity_state.json",
        {"dynamic": {"presence": "available"}},
    )
    _write_space(home / "spaces" / "finanzjunkie", slug="finanzjunkie", name="Finanzjunkie", revision=2, enrolled=False)
    _write_space(home / "spaces" / "aquarium-zentrum", slug="aquarium-zentrum", name="Aquarium Zentrum", revision=2, enrolled=True)
    _write_ledger(home / "state" / "nova-space-supervisor.sqlite")

    payload = build_presence_card(home=home)

    assert [item["space"] for item in payload["managed_spaces"]] == ["aquarium-zentrum"]
    rendered = json.dumps(payload)
    assert "finanzjunkie" not in rendered
    assert "nova_data" not in rendered

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


def test_presence_card_projects_redacted_model_catalog_blocker_reason():
    from web.api.nova_presence import _blockers_for

    blockers = _blockers_for(
        [{"space": "alpha", "state": "paused"}],
        [{"space": "alpha", "reason": "no_eligible_model"}],
    )
    assert blockers == [{"space": "alpha", "code": "model_catalog_unavailable"}]


@pytest.mark.parametrize("reason", ["provider_unavailable", "model_provider_unavailable"])
def test_presence_card_projects_provider_pause_reason_without_private_data(reason):
    from web.api.nova_presence import _blockers_for

    blockers = _blockers_for([{ "space": "alpha", "state": "active" }], [{ "space": "alpha", "reason": reason, "private_reason": "C:/secret" }])

    assert blockers == [{"space": "alpha", "code": "model_provider_unavailable"}]

def test_presence_card_projects_active_limit_as_global_slot_blocker():
    """A coalesced signal must explain slot contention, not hide behind paused."""
    from web.api.nova_presence import _blockers_for

    blockers = _blockers_for(
        [{"space": "alpha", "state": "paused"}],
        [{"space": "alpha", "reason": "active_limit"}],
    )

    assert blockers == [{"space": "alpha", "code": "global_run_slot_busy"}]



def test_presence_card_projects_durable_global_slot_owner_and_skipped_tick(
    tmp_path: Path,
):
    """Presence exposes only a durable managed owner and redacted no-TTL state."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    space_id, root_fingerprint = _write_marker_space(home / "spaces" / "alpha", slug="alpha")
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)
    _write_scheduler_marker_state(
        ledger,
        space="alpha",
        space_id=space_id,
        root_fingerprint=root_fingerprint,
        revision=7,
        pending_digest="d" * 64,
        last_check_code="active_limit",
        last_checked_at=123.0,
    )

    payload = build_presence_card(home=home)

    assert payload["global_run_slot"] == {
        "state": "occupied",
        "occupied_by": "alpha",
        "occupied_at": "2026-07-29T10:05:00+00:00",
        "expires_at": None,
    }
    assert {item["code"] for item in payload["blockers"]} == {"global_run_slot_busy"}
    assert payload["blockers"] == [{"space": "alpha", "code": "global_run_slot_busy"}]

def test_presence_card_projects_pending_actions_without_writing(tmp_path):
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    space_id, root_fingerprint = _write_marker_space(home / "spaces" / "alpha", slug="alpha")
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)
    _write_scheduler_marker_state(
        ledger,
        space="alpha",
        space_id=space_id,
        root_fingerprint=root_fingerprint,
        revision=7,
    )
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            "UPDATE nova_supervision_space_state SET pending_count = 3 WHERE target_key = 'alpha'"
        )
    before = _tree_snapshot(home)

    payload = build_presence_card(home=home)

    assert _tree_snapshot(home) == before
    assert payload["pending_actions"] == 1
    assert payload["pending_signals"] == 3
    assert payload["managed_spaces"][0]["pending_actions"] == 1
    assert payload["managed_spaces"][0]["pending_signals"] == 3


def test_presence_card_projects_active_host_ticker_without_leaking_lease_owner(tmp_path):
    """The Nova entity card must distinguish an active host ticker from idle."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    _write_marker_space(home / "spaces" / "alpha", slug="alpha")
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            """INSERT INTO supervisor_ticker_leases (
                lease_id, owner_id, state, started_at, expires_at, updated_at, terminal_reason
            ) VALUES (?, ?, 'active', ?, ?, ?, NULL)""",
            (
                "lease-private-secret",
                "host-private-secret",
                1_800_000_000.0,
                2_000_000_000.0,
                1_800_000_060.0,
            ),
        )
    before = _tree_snapshot(home)

    payload = build_presence_card(home=home)

    assert _tree_snapshot(home) == before
    assert payload["supervision"] == {
        "running": True,
        "last_pulse_at": "2027-01-15T08:01:00+00:00",
        "lease": {"state": "active", "liveness": "lease_unverified"},
    }
    assert "private-secret" not in json.dumps(payload)
def test_presence_card_lifts_human_release_slot_to_payload_root(tmp_path, monkeypatch):
    """The UI release control reads the opaque slot from the payload root."""
    from web.api import nova_presence

    home = tmp_path / "home"
    _write_marker_space(home / "spaces" / "alpha", slug="alpha")
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)

    monkeypatch.setattr(
        nova_presence,
        "_managed_space_summaries",
        lambda _root: [{"space": "alpha", "name": "Alpha", "governance_revision": 7, "state": "idle"}],
    )
    monkeypatch.setattr(
        nova_presence,
        "_read_supervisor_admissions",
        lambda _path, _spaces: [{
            "admission_id": "admission-alpha",
            "space": "alpha",
            "state": "paused",
            "run_id": "123e4567-e89b-12d3-a456-426614174000",
            "canonical_root": "C:/private/project",
            "at": "2026-07-29T10:05:00+00:00",
        }],
    )

    monkeypatch.setattr(
        nova_presence,
        "_release_slot_for",
        lambda admission: (
            {"run_id": "123e4567-e89b-12d3-a456-426614174000", "space": "alpha"}
            if admission.get("state") == "paused"
            else None
        ),
    )

    payload = nova_presence.build_presence_card(home=home)

    assert payload["release_slot"] == {
        "run_id": "123e4567-e89b-12d3-a456-426614174000",
        "space": "alpha",
    }
    assert payload["managed_spaces"][0]["release_slot"] == payload["release_slot"]


def test_presence_ticker_projection_deduplicates_repeated_event_ids(tmp_path):
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    space_id, root_fingerprint = _write_marker_space(home / "spaces" / "alpha", slug="alpha")
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)
    _write_scheduler_marker_state(
        ledger,
        space="alpha",
        space_id=space_id,
        root_fingerprint=root_fingerprint,
        revision=7,
    )
    event_log = ledger.with_name("ticker_events.jsonl")
    event_log.write_text(
        "\n".join(
            [
                '{"event_id":"abcdef0123456789abcdef0123456789","space":"alpha","source":"bridge","stage":"handled","status":"failed","reason":"active_limit","at":"1"}',
                '{"event_id":"abcdef0123456789abcdef0123456789","space":"alpha","source":"bridge","stage":"handled","status":"failed","reason":"active_limit","at":"2"}',
                '{"event_id":"1234567890abcdef1234567890abcdef","space":"alpha","source":"heartbeat","stage":"observed","status":"pending","reason":"periodic_check","at":"3"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_presence_card(home=home)
    assert len(payload["ticker_events"]) == 2
    assert payload["ticker_events"][0]["at"] == "3"
    assert len(payload["unread_events"]) == 2
    assert [item["status"] for item in payload["unread_events"]] == ["pending", "failed"]


def test_presence_card_projects_model_chain_and_deployment_blockers():
    from web.api.nova_presence import _blockers_for

    blockers = _blockers_for(
        [{"space": "alpha", "state": "paused"}, {"space": "beta", "state": "paused"}],
        [
            {"space": "alpha", "reason": "model_chain_exhausted"},
            {"space": "beta", "reason": "deployment_unverified"},
        ],
    )

    assert blockers == [
        {"space": "alpha", "code": "model_chain_exhausted"},
        {"space": "beta", "code": "deployment_unverified"},
    ]


def test_presence_card_surfaces_space_binding_revocation_as_actionable_codes():
    """Governance/root deletion pauses must remain visible in the entity card."""
    from web.api.nova_presence import _blockers_for

    blockers = _blockers_for(
        [
            {"space": "alpha", "state": "paused"},
            {"space": "beta", "state": "paused"},
            {"space": "gamma", "state": "paused"},
        ],
        [
            {"space": "alpha", "reason": "governance_changed"},
            {"space": "beta", "reason": "root_changed"},
            {"space": "gamma", "reason": "space_deleted"},
        ],
    )

    assert blockers == [
        {"space": "alpha", "code": "governance_changed"},
        {"space": "beta", "code": "root_changed"},
        {"space": "gamma", "code": "space_deleted"},
    ]


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
        ("pending_digest", "malformed"),
        ("pending_digest", None),
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


@pytest.mark.parametrize("mutation", ("root_changed", "untrusted_root", "truncated_audit"))
def test_presence_card_omits_marker_when_current_governance_binding_cannot_be_proven(
    tmp_path, mutation: str
):
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    space_path = home / "spaces" / "alpha"
    space_id, root_fingerprint = _write_marker_space(space_path, slug="alpha")
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
    config_path = space_path / "space.yaml"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    if mutation == "root_changed":
        replacement = home / "replacement-trusted-root"
        replacement.mkdir()
        config["project_dir"] = str(replacement.resolve())
    elif mutation == "untrusted_root":
        config["project_dir"] = str(home / "missing-untrusted-root")
    else:
        config["nova_management_audit"] = [config["nova_management_audit"][-1]]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    assert build_presence_card(home=home)["change_markers"] == []


def test_presence_card_omits_yolo_space_without_independent_enrollment_binding(tmp_path):
    """A copied YOLO flag must not make an unbound Space look supervised."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    space = home / "spaces" / "spoofed"
    space.mkdir(parents=True)
    (space / "space.yaml").write_text(
        "\n".join(
            [
                "nova_management:",
                "  yolo: true",
                "  enrolled: true",
                "  revision: 4",
                "project_dir: C:/private/not-attested",
                "",
            ]
        ),
        encoding="utf-8",
    )

    payload = build_presence_card(home=home)

    assert payload["managed_spaces"] == []
    assert payload["focus"]["kind"] == "presence"


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


def test_presence_card_treats_a_live_sqlite_wal_as_unknown_without_side_effects(tmp_path):
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

    connection = sqlite3.connect(ledger)
    try:
        assert connection.execute("PRAGMA journal_mode = WAL").fetchone()[0].lower() == "wal"
        connection.execute("UPDATE nova_supervision_space_state SET updated_at = 2.0")
        connection.commit()
        assert ledger.with_name(ledger.name + "-wal").is_file()
        before = _tree_snapshot(home)

        assert build_presence_card(home=home)["change_markers"] == []
        assert _tree_snapshot(home) == before
    finally:
        connection.close()


def test_presence_card_treats_an_open_rollback_journal_as_unknown_without_side_effects(
    tmp_path,
):
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

    writer = sqlite3.connect(ledger, isolation_level=None)
    try:
        assert writer.execute("PRAGMA journal_mode = DELETE").fetchone()[0].lower() == "delete"
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE nova_supervision_space_state SET updated_at = 2.0")
        assert ledger.with_name(ledger.name + "-journal").is_file()
        before = _tree_snapshot(home)

        assert build_presence_card(home=home)["change_markers"] == []
        assert _tree_snapshot(home) == before
    finally:
        writer.execute("ROLLBACK")
        writer.close()


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
    assert authorized.json()["entity_feed"] == []
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

@pytest.fixture(autouse=True)
def _allow_synthetic_test_roots(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register repository-local fixture roots with the read-only trust seam.

    Production enrollment still requires the live trusted-workspace registry;
    this test-only adapter lets synthetic spaces under ``.test-tmp`` exercise
    the rest of the presence projection without weakening that production
    gate. Missing or outside roots continue through the real resolver.
    """
    import web.api.workspace as workspace

    original = workspace.resolve_enrollment_trusted_workspace_read_only
    fixture_root = (Path(__file__).resolve().parent.parent / ".test-tmp").resolve()
    def resolve(value: object) -> Path:
        try:
            return original(value)
        except ValueError:
            candidate = Path(str(value)).expanduser().resolve()
            try:
                candidate.relative_to(fixture_root)
            except ValueError:
                raise
            if not candidate.is_dir():
                raise
            return candidate

    monkeypatch.setattr(
        workspace,
        "resolve_enrollment_trusted_workspace_read_only",
        resolve,
    )

def test_presence_unread_includes_durable_resonance_without_double_counting(tmp_path: Path, monkeypatch):
    """Rotated ticker events still drive the entity attention badge once."""
    from web.api.nova_presence import build_presence_card
    monkeypatch.setattr("web.api.workspace.resolve_enrollment_trusted_workspace_read_only", lambda value: Path(value))
    home = tmp_path / "home"
    _write_marker_space(home / "spaces" / "alpha", slug="alpha")
    resonance = home / "state" / "resonance_memory.sqlite"
    resonance.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(resonance) as connection:
        connection.execute(
            "CREATE TABLE resonance_events (event_id TEXT PRIMARY KEY, space TEXT, source TEXT, stage TEXT, status TEXT, reason TEXT, observed_at REAL)"
        )
        connection.executemany(
            "INSERT INTO resonance_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("a" * 64, "alpha", "ci", "handled", "failed", "ci_failed", 20.0),
                ("b" * 64, "alpha", "git", "handled", "handled", "git_change", 10.0),
                ("c" * 64, "alpha", "heartbeat", "observed", "pending", "periodic_check", 30.0),
            ],
        )
    payload = build_presence_card(home=home)
    assert [item["event_id"] for item in payload["unread_events"]] == ["c" * 64, "a" * 64]
    assert payload["unread_event_count"] == 2
    assert all(item["status"] in {"pending", "failed"} for item in payload["unread_events"])
def test_presence_card_projects_resonance_entity_feed_read_only_and_redacted(tmp_path: Path, monkeypatch):
    from web.api.nova_presence import build_presence_card
    monkeypatch.setattr("web.api.workspace.resolve_enrollment_trusted_workspace_read_only", lambda value: Path(value))

    home = tmp_path / "home"
    _write_marker_space(home / "spaces" / "alpha", slug="alpha")
    _write_marker_space(home / "spaces" / "beta", slug="beta")
    resonance = home / "state" / "resonance_memory.sqlite"
    resonance.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(resonance) as connection:
        connection.execute(
            "CREATE TABLE resonance_events (event_id TEXT PRIMARY KEY, space TEXT, source TEXT, stage TEXT, status TEXT, reason TEXT, observed_at REAL)"
        )
        connection.executemany(
            "INSERT INTO resonance_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("a" * 64, "alpha", "bridge", "handled", "failed", "skipped_slot_occupied", 20.0),
                ("b" * 64, "beta", "git", "handled", "handled", "git_change", 10.0),
                ("c" * 64, "not-enrolled", "ci", "handled", "failed", "ci_failed", 30.0),
            ],
        )
    before = _tree_snapshot(home)
    payload = build_presence_card(home=home)
    assert _tree_snapshot(home) == before
    assert payload["entity_feed"] == [
        {
            "event_id": "a" * 64,
            "space": "alpha",
            "source": "bridge",
            "stage": "handled",
            "status": "failed",
            "reason": "skipped_slot_occupied",
            "at": "1970-01-01T00:00:20+00:00",
        },
        {
            "event_id": "b" * 64,
            "space": "beta",
            "source": "git",
            "stage": "handled",
            "status": "handled",
            "reason": "git_change",
            "at": "1970-01-01T00:00:10+00:00",
        },
    ]
    assert "not-enrolled" not in json.dumps(payload["entity_feed"])
    assert str(home) not in json.dumps(payload["entity_feed"])


def test_presence_card_omits_tombstoned_resonance_from_feed_and_unread(
    tmp_path: Path, monkeypatch
):
    """Revoked resonance must not reappear through the rotated ticker path."""
    from web.api.nova_presence import build_presence_card

    monkeypatch.setattr(
        "web.api.workspace.resolve_enrollment_trusted_workspace_read_only",
        lambda value: Path(value),
    )
    home = tmp_path / "home"
    _write_marker_space(home / "spaces" / "alpha", slug="alpha")
    resonance = home / "state" / "resonance_memory.sqlite"
    resonance.parent.mkdir(parents=True, exist_ok=True)
    revoked = "a" * 64
    visible = "b" * 64
    with sqlite3.connect(resonance) as connection:
        connection.execute(
            "CREATE TABLE resonance_events (event_id TEXT PRIMARY KEY, space TEXT, source TEXT, stage TEXT, status TEXT, reason TEXT, observed_at REAL)"
        )
        connection.execute(
            "CREATE TABLE resonance_entity_tombstone (event_id TEXT PRIMARY KEY, tombstoned_at REAL NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO resonance_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (revoked, "alpha", "ci", "handled", "failed", "ci_failed", 20.0),
                (visible, "alpha", "ci", "handled", "failed", "ci_failed", 10.0),
            ],
        )
        connection.execute(
            "INSERT INTO resonance_entity_tombstone VALUES (?, ?)",
            (revoked, 21.0),
        )
    ticker = home / "state" / "ticker_events.jsonl"
    ticker.write_text(
        "\n".join(
            json.dumps(
                {
                    "event_id": event_id,
                    "space": "alpha",
                    "source": "ci",
                    "stage": "handled",
                    "status": "failed",
                    "reason": "ci_failed",
                    "at": str(at),
                }
            )
            for event_id, at in ((revoked, 20), (visible, 10))
        )
        + "\n",
        encoding="utf-8",
    )

    payload = build_presence_card(home=home)

    assert [item["event_id"] for item in payload["entity_feed"]] == [visible]
    assert [item["event_id"] for item in payload["unread_events"]] == [visible]


def test_presence_card_fails_closed_for_duplicate_space_identity_and_root(tmp_path: Path, monkeypatch):
    """A copied valid enrollment must not create a second managed Space."""
    from web.api.nova_presence import build_presence_card
    monkeypatch.setattr("web.api.workspace.resolve_enrollment_trusted_workspace_read_only", lambda value: Path(value))
    home = tmp_path / "home"
    aquarium = home / "spaces" / "aquarium-zentrum"
    finanzjunkie = home / "spaces" / "finanzjunkie"
    _write_marker_space(aquarium, slug="aquarium-zentrum")
    finanzjunkie.mkdir(parents=True)
    (finanzjunkie / "space.yaml").write_text((aquarium / "space.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    ticker_path = home / "state" / "ticker_events.jsonl"
    ticker_path.parent.mkdir(parents=True, exist_ok=True)
    ticker_path.write_text("\n".join(json.dumps(item) for item in (
        {"event_id":"a"*64,"space":"aquarium-zentrum","source":"ci","stage":"handled","status":"failed","reason":"ci_failed","at":"2026-08-03T10:00:00+00:00"},
        {"event_id":"b"*64,"space":"finanzjunkie","source":"ci","stage":"handled","status":"failed","reason":"ci_failed","at":"2026-08-03T10:01:00+00:00"},
    )), encoding="utf-8")
    payload = build_presence_card(home=home)
    from web.api.nova_presence import _managed_space_marker_bindings
    assert list(_managed_space_marker_bindings(home / "spaces")) == ["aquarium-zentrum"]
    assert [item["space"] for item in payload["managed_spaces"]] == ["aquarium-zentrum"]
    assert [item["space"] for item in payload["ticker_events"]] == ["aquarium-zentrum"]
    assert "finanzjunkie" not in json.dumps(payload)

def test_marker_bindings_reject_duplicate_identity_components(tmp_path: Path, monkeypatch):
    from web.api.nova_presence import _managed_space_marker_bindings
    spaces = tmp_path / "spaces"
    for slug in ("aquarium-zentrum", "finanzjunkie"):
        (spaces / slug).mkdir(parents=True)
        (spaces / slug / "space.yaml").write_text("name: " + slug, encoding="utf-8")
    monkeypatch.setattr(
        "web.api.nova_presence._marker_binding_from_config",
        lambda config: {
            "space_id": "same-space-id",
            "root_fingerprint": "root-" + str(config.get("name")),
            "governance_revision": 1,
        },
    )
    assert list(_managed_space_marker_bindings(spaces)) == ["aquarium-zentrum"]


def test_marker_bindings_reject_duplicate_trusted_root_with_distinct_space_ids(
    tmp_path: Path, monkeypatch
):
    """A copied trusted root cannot make two distinct Spaces look managed.

    Space identity and root identity are independent admission components. A
    forged second YAML may mint another UUID while still pointing at the same
    trusted project root; the public projection must keep the first stable
    binding only instead of exposing two autonomous owners for one checkout.
    """
    from web.api.nova_presence import _managed_space_marker_bindings

    spaces = tmp_path / "spaces"
    for slug in ("aquarium-zentrum", "finanzjunkie"):
        (spaces / slug).mkdir(parents=True)
        (spaces / slug / "space.yaml").write_text("name: " + slug, encoding="utf-8")

    monkeypatch.setattr(
        "web.api.nova_presence._marker_binding_from_config",
        lambda config: {
            "space_id": "id-" + str(config.get("name")),
            "root_fingerprint": "same-trusted-root",
            "governance_revision": 1,
        },
    )

    assert list(_managed_space_marker_bindings(spaces)) == ["aquarium-zentrum"]


def test_three_space_yolo_admission_isolation_and_entity_feed_are_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Only the enrolled Aquarium Space can occupy Nova's global run slot.

    This joins the supervisor admission boundary with Nova's public, read-only
    entity projection: Nova and Finanzjunkie stay out, repeated Aquarium
    signals coalesce, and the feed exposes only the redacted Aquarium event.
    """
    from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
    from web.api.nova_presence import build_presence_card

    monkeypatch.setattr(
        "web.api.workspace.resolve_enrollment_trusted_workspace_read_only",
        lambda value: Path(value),
    )
    home = tmp_path / "home"
    _write_json(
        home / "spaces" / "nova" / "nova_data" / "entity" / "entity_state.json",
        {"dynamic": {"presence": "available"}},
    )
    _write_space(
        home / "spaces" / "finanzjunkie",
        slug="finanzjunkie",
        name="Finanzjunkie",
        revision=2,
        enrolled=False,
    )
    aquarium = home / "spaces" / "aquarium-zentrum"
    aquarium_id, aquarium_fp = _write_marker_space(
        aquarium, slug="aquarium-zentrum", revision=2
    )
    aquarium_root = aquarium / "trusted-project"
    records = {
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(
            space_id=aquarium_id,
            canonical_root=aquarium_root,
            root_fingerprint=aquarium_fp,
            yolo=True,
            enrolled=True,
            revision=2,
            policy_identity="policy:aquarium-v2",
        ),
        "finanzjunkie": ManagedSpaceGovernance.from_values(
            space_id=uuid4(),
            canonical_root=home / "spaces" / "finanzjunkie" / "trusted-project",
            root_fingerprint="",
            yolo=False,
            enrolled=False,
            revision=2,
            policy_identity="policy:finanzjunkie-v2",
        ),
    }
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    supervisor = ManagedSpaceSupervisor(
        ledger_path=ledger,
        governance_resolver=lambda target: records.get(target),
    )

    rejected_finanz = supervisor.admit(
        "finanzjunkie", {"goal": "inspect portfolio", "kind": "maintenance"}
    )
    first = supervisor.admit(
        "aquarium-zentrum", {"goal": "check water quality", "kind": "maintenance"}
    )
    assert rejected_finanz.status == "rejected"
    assert rejected_finanz.reason == "not_yolo_enrolled"
    assert first.status == "created" and first.run_id and first.capability
    assert supervisor.start_admitted_run(first.capability, dispatcher=lambda *_: None)
    repeated = supervisor.admit(
        "aquarium-zentrum", {"goal": "check water quality", "kind": "maintenance"}
    )
    assert repeated.status == "coalesced"
    assert repeated.run_id == first.run_id
    assert len(supervisor.list_active_admissions()) == 1

    resonance = home / "state" / "resonance_memory.sqlite"
    resonance.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(resonance) as connection:
        connection.execute(
            "CREATE TABLE resonance_events (event_id TEXT PRIMARY KEY, space TEXT, source TEXT, stage TEXT, status TEXT, reason TEXT, observed_at REAL)"
        )
        connection.executemany(
            "INSERT INTO resonance_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    "a" * 64,
                    "aquarium-zentrum",
                    "ci",
                    "handled",
                    "pending",
                    "water_check",
                    20.0,
                ),
                (
                    "b" * 64,
                    "finanzjunkie",
                    "ci",
                    "handled",
                    "failed",
                    "private",
                    21.0,
                ),
                (
                    "c" * 64,
                    "nova",
                    "bridge",
                    "handled",
                    "failed",
                    "private",
                    22.0,
                ),
            ],
        )
    before = _tree_snapshot(home)
    payload = build_presence_card(home=home)
    assert _tree_snapshot(home) == before
    assert [item["space"] for item in payload["managed_spaces"]] == [
        "aquarium-zentrum"
    ]
    assert payload["global_run_slot"]["state"] == "occupied"
    assert payload["global_run_slot"]["occupied_by"] == "aquarium-zentrum"
    assert [item["space"] for item in payload["entity_feed"]] == ["aquarium-zentrum"]
    rendered = json.dumps(payload)
    assert "finanzjunkie" not in rendered
    assert "private" not in rendered
    assert str(home) not in rendered


def test_operational_projection_exposes_bounded_next_step_for_paused_model_chain():
    from web.api.nova_presence import _operational_projection

    projection = _operational_projection(
        managed_spaces=[{"space": "aquarium-zentrum"}],
        blockers=[{"space": "aquarium-zentrum", "code": "model_chain_exhausted"}],
        supervision={"running": True},
    )

    assert projection["runtime_status"] == "degraded"
    assert projection["next_step_code"] == "refresh_ollama_catalog"
    assert "path" not in str(projection).lower()
    assert "model" not in projection["next_step_code"]


def test_presence_identity_and_paused_next_step_survive_host_restart_without_cross_space_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two independent read projections retain Nova identity and blocker state."""
    from web.api.nova_presence import build_presence_card

    home = tmp_path / "home"
    nova_root = home / "spaces" / "nova"
    _write_json(
        nova_root / "nova_data" / "entity" / "entity_state.json",
        {"dynamic": {"presence": "thinking"}},
    )
    _write_space(home / "spaces" / "finanzjunkie", slug="finanzjunkie", name="Finanzjunkie", revision=2, enrolled=False)
    aquarium = home / "spaces" / "aquarium-zentrum"
    aquarium_id, aquarium_fp = _write_marker_space(aquarium, slug="aquarium-zentrum", revision=2)
    aquarium_root = aquarium / "trusted-project"
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    _write_ledger(ledger)
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            "UPDATE supervisor_admissions SET target_key=?, target_space_id=?, canonical_root=?, root_fingerprint=? WHERE admission_id=?",
            ("aquarium-zentrum", aquarium_id, str(aquarium_root.resolve()), aquarium_fp, "admission-secret-id"),
        )
        connection.execute(
            "UPDATE supervisor_audit SET reason=? WHERE admission_id=?",
            ("model_chain_exhausted", "admission-secret-id"),
        )
    monkeypatch.setattr(
        "web.api.workspace.resolve_enrollment_trusted_workspace_read_only",
        lambda value: Path(value),
    )

    first = build_presence_card(home=home)
    # Simulate a host restart: the second projection reconstructs all facts from
    # the persisted ledger and Space config, without a ticker/model invocation.
    second = build_presence_card(home=home)
    for payload in (first, second):
        assert payload["identity"] == {"name": "Nova", "voice": "direct, curious, accountable"}
        assert [item["space"] for item in payload["managed_spaces"]] == ["aquarium-zentrum"]
        assert payload["managed_spaces"][0]["state"] == "paused"
        assert payload["focus"] == {"kind": "supervision", "space": "aquarium-zentrum", "state": "paused"}
        assert payload["blockers"] == [{"space": "aquarium-zentrum", "code": "model_chain_exhausted"}]
        assert payload["operational"]["next_step_code"] == "refresh_ollama_catalog"
        rendered = json.dumps(payload)
        assert "finanzjunkie" not in rendered
        assert "space-alpha" not in rendered
        assert "C:/private" not in rendered