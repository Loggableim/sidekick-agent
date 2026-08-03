"""Deterministic host-level continuous YOLO smoke across the three product Spaces.

This deliberately uses only temporary roots, the in-process supervisor, and a
fake dispatcher.  It proves the host contract without starting the WebUI,
providers, models, GitHub, or deploy workers.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
import sqlite3
from uuid import uuid4

from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime
from nova.space_supervisor import (
    SYSTEM_SPACE_LIFECYCLE_ACTOR,
    ManagedSpaceGovernance,
    ManagedSpaceSupervisor,
)
from swarm_core.engine import PreCompletionContext
from swarm_core.store import ProjectSwarmStore


def _write_enrolled_marker(space_root: Path, *, slug: str, revision: int = 2) -> tuple[str, Path]:
    """Write the same chained, trusted enrollment evidence as production."""
    space_root.mkdir(parents=True, exist_ok=True)
    project_root = space_root / "trusted-project"
    project_root.mkdir()
    space_id = uuid4().hex
    root_fingerprint = hashlib.sha256(
        str(project_root.resolve()).encode("utf-8")
    ).hexdigest()
    audit: list[dict[str, object]] = []
    previous = {"yolo": False, "enrolled": False, "revision": 0}
    for event_revision in range(1, revision + 1):
        following = {
            "yolo": event_revision == revision,
            "enrolled": event_revision == revision,
            "revision": event_revision,
        }
        audit.append(
            {
                "actor": "dashboard:" + "a" * 64,
                "timestamp": float(event_revision),
                "space_id": space_id,
                "root_fingerprint": root_fingerprint if event_revision == revision else "",
                "policy_revision": event_revision,
                "governance_revision": event_revision,
                "previous": previous,
                "next": following,
            }
        )
        previous = following
    (space_root / "space.yaml").write_text(
        json.dumps(
            {
                "name": slug,
                "project_dir": str(project_root.resolve()),
                "space_id": space_id,
                "nova_management": {"yolo": True, "enrolled": True, "revision": revision},
                "nova_management_audit": audit,
            }
        ),
        encoding="utf-8",
    )
    return space_id, project_root


def test_host_three_space_continuous_yolo_e2e_and_read_only_presence(
    tmp_path: Path, monkeypatch
) -> None:
    """Only Aquarium may run; its fake run completes and the card stays pure."""
    home = tmp_path / "home"
    spaces_root = home / "spaces"
    nova_root = spaces_root / "nova"
    nova_root.mkdir(parents=True)
    (nova_root / "nova_data" / "entity" / "entity_state.json").parent.mkdir(parents=True)
    (nova_root / "nova_data" / "entity" / "entity_state.json").write_text(
        json.dumps({"dynamic": {"presence": "available"}}), encoding="utf-8"
    )

    aquarium_id, aquarium_project = _write_enrolled_marker(
        spaces_root / "aquarium-zentrum", slug="aquarium-zentrum"
    )
    finance_id, finance_project = _write_enrolled_marker(
        spaces_root / "finanzjunkie", slug="finanzjunkie"
    )
    # The fixture is intentionally present but not enrolled: a global YOLO
    # switch or a stale signal must never make this Space autonomous.
    finance_config = json.loads(
        (spaces_root / "finanzjunkie" / "space.yaml").read_text(encoding="utf-8")
    )
    finance_config["nova_management"] = {"yolo": True, "enrolled": False, "revision": 2}
    (spaces_root / "finanzjunkie" / "space.yaml").write_text(
        json.dumps(finance_config), encoding="utf-8"
    )
    nova_id = uuid4().hex

    governance = {
        "nova": ManagedSpaceGovernance.from_values(
            space_id=nova_id,
            canonical_root=nova_root,
            yolo=False,
            enrolled=True,
            revision=1,
            policy_identity="space-governance:test",
        ),
        "finanzjunkie": ManagedSpaceGovernance.from_values(
            space_id=finance_id,
            canonical_root=finance_project,
            yolo=True,
            enrolled=False,
            revision=2,
            policy_identity="space-governance:test",
        ),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(
            space_id=aquarium_id,
            canonical_root=aquarium_project,
            yolo=True,
            enrolled=True,
            revision=2,
            policy_identity="space-governance:test",
        ),
    }
    ledger = home / "state" / "nova-space-supervisor.sqlite"
    supervisor = ManagedSpaceSupervisor(
        ledger_path=ledger,
        governance_resolver=lambda target: governance.get(target),
    )
    dispatched: list[tuple[Path, str]] = []

    def fake_dispatch(root: Path, run_id: str) -> None:
        dispatched.append((root, run_id))
        # The host fake is the only worker boundary in this test.  It performs
        # no model/tool call, then reports durable completion exactly once.
        ProjectSwarmStore(root).set_run_status(run_id, "completed")
        assert supervisor.record_completion(run_id) is True
        assert supervisor.record_completion(run_id) is False

    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=fake_dispatch,
    )
    assert runtime.ingest_signal(
        "nova", source="git", event_id="nova-1", reason_code="git_change"
    ) is False
    assert runtime.ingest_signal(
        "finanzjunkie", source="ci", event_id="finance-1", reason_code="ci_failed"
    ) is False
    assert runtime.ingest_signal(
        "aquarium-zentrum", source="git", event_id="aquarium-1", reason_code="git_change"
    ) is True
    # A duplicate edge and the same event from the host pulse are coalesced.
    assert runtime.ingest_signal(
        "aquarium-zentrum", source="git", event_id="aquarium-1", reason_code="git_change"
    ) is False
    outcomes = runtime.pulse(now_epoch=100.0)
    assert [(item.target_key, item.status) for item in outcomes] == [
        ("aquarium-zentrum", "started")
    ]
    assert len(dispatched) == 1
    assert supervisor.list_active_admissions() == []
    assert ProjectSwarmStore(aquarium_project).get_run(dispatched[0][1]).status == "completed"
    assert runtime.pulse(now_epoch=101.0) == ()

    # Presence is a read-only projection of the same completed ledger.  The
    # snapshot must expose Aquarium only and never leak roots, IDs, or fixture
    # data for Nova/Finanzjunkie.
    from web.api.nova_presence import build_presence_card
    # Synthetic fixture roots are not part of production trust registry.
    monkeypatch.setattr(
        "web.api.workspace.resolve_enrollment_trusted_workspace_read_only",
        lambda value: Path(value),
    )


    # Cross-Space ticker events are present in the shared ledger, but the
    # public projection must admit only the enrolled YOLO Space.
    ledger.with_name("ticker_events.jsonl").write_text(
        json.dumps({
            "event_id": "finance-cross-space-001",
            "space": "finanzjunkie",
            "source": "ci",
            "stage": "handled",
            "status": "failed",
            "reason": "ci_failed",
            "at": "2026-08-03T10:01:00+00:00",
        })
        + "\n"
        + json.dumps({
            "event_id": "nova-cross-space-001",
            "space": "nova",
            "source": "git",
            "stage": "handled",
            "status": "failed",
            "reason": "git_change",
            "at": "2026-08-03T10:02:00+00:00",
        })
        + "\n",
        encoding="utf-8",
    )
    before = {
        str(path.relative_to(home)): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    payload = build_presence_card(home=home)
    after = {
        str(path.relative_to(home)): path.read_bytes()
        for path in home.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert [item["space"] for item in payload["managed_spaces"]] == ["aquarium-zentrum"]
    assert payload["managed_spaces"][0]["state"] == "completed"
    rendered = json.dumps(payload)
    assert "finanzjunkie" not in rendered
    assert str(aquarium_project) not in rendered
    assert aquarium_id not in rendered
    assert "run_id" not in payload["activity"][0]

    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM supervisor_admissions WHERE state IN "
            "('provisioning','active','paused','cancelling','abandoning')"
        ).fetchone()[0] == 0


def test_controlled_yolo_harness_admission_fake_ollama_review_and_completion(
    tmp_path: Path, monkeypatch
) -> None:
    """Exercise the authorized test-Space contract without a live listener.

    The fake dispatcher is the only model boundary. It records bounded fake
    Ollama/review evidence, then the supervisor's pre-completion hook performs
    the same capability and verifier gate used by production workers.
    """
    project_root = tmp_path / "spaces" / "aquarium-zentrum"
    project_root.mkdir(parents=True)
    governance = ManagedSpaceGovernance.from_values(
        space_id=uuid4().hex,
        canonical_root=project_root,
        root_fingerprint="",
        yolo=True,
        enrolled=True,
        revision=1,
        policy_identity="space-governance:controlled-test",
    )
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=lambda target: governance if target == "aquarium-zentrum" else None,
    )
    calls: list[str] = []

    # A foreign host lease may be present while the listener is offline. The
    # controlled harness must never reclaim it as part of startup readiness.
    from cli.web_server import _dashboard_execution_owner_alive
    assert supervisor.acquire_ticker_lease("worker:foreign", now=0.0, ttl_seconds=60.0)
    assert supervisor.reconcile_stale_ticker_leases(
        _dashboard_execution_owner_alive, now=30.0
    ) == ()

    def fake_dispatch(root: Path, run_id: str) -> None:
        assert root == project_root
        calls.append("ollama-cloud:fake")
        store = ProjectSwarmStore(root)
        for role, model, decision in (
            ("builder", "minimax-m3", "approve"),
            ("critic", "minimax-m3", "approve"),
            ("reviewer_a", "glm-5.2", "approve"),
            ("reviewer_b", "kimi-k2.7-code", "approve"),
            ("verifier", "deepseek-v4-flash", "verified"),
        ):
            store.record_workflow_role_checkpoint(
                run_id, role, model=model,
                data={
                    "work": f"{role} fake evidence",
                    "evidence": [f"fake:{role}:ok"],
                    "decision": decision,
                    "provenance": {"adapter": "controlled-test", "mode": "fake"},
                },
            )
        # The child reaches completed only after fake model/review evidence.
        assert store.set_run_status(run_id, "completed").status == "completed"

    admission = supervisor.admit(
        "aquarium-zentrum", {"goal": "controlled test-space maintenance", "kind": "maintenance"}
    )
    assert admission.status == "created" and admission.capability is not None
    assert supervisor.start_admitted_run(admission.capability, dispatcher=fake_dispatch)
    assert calls == ["ollama-cloud:fake"]

    # The final verifier is explicitly faked for this harness; no provider or
    # network call is allowed.
    monkeypatch.setattr(
        "nova.production_verifier.ProductionReadOnlyVerifier.verify",
        lambda self, request: type("Result", (), {"decision": "verified"})(),
    )
    store = ProjectSwarmStore(project_root)
    completed = store.get_run(admission.run_id)
    assert completed is not None
    outcome = supervisor.pre_completion_hook_for_run(admission.run_id).run(
        PreCompletionContext(
            run=completed, project_root=project_root, store=store,
            goal="controlled test-space maintenance", pack="coding-team",
            autonomy="autonomous", call_count=1, decision="verified", evidence={},
        )
    )
    assert outcome.continue_completion is True
    assert supervisor.record_completion(admission.run_id) is True
    assert supervisor.record_completion(admission.run_id) is False
    assert supervisor.list_active_admissions() == []


def test_controlled_yolo_harness_pauses_on_failed_fake_verifier(tmp_path: Path) -> None:
    """A controlled test run with failed verification stays paused and owned."""
    project_root = tmp_path / "spaces" / "aquarium-zentrum"
    project_root.mkdir(parents=True)
    governance = ManagedSpaceGovernance.from_values(
        space_id=uuid4().hex, canonical_root=project_root, root_fingerprint="",
        yolo=True, enrolled=True, revision=1, policy_identity="policy:controlled-test",
    )
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=lambda _target: governance,
    )
    admission = supervisor.admit("aquarium-zentrum", {"goal": "controlled verification", "kind": "maintenance"})
    assert admission.capability is not None
    assert supervisor.start_admitted_run(admission.capability, dispatcher=lambda root, run_id: ProjectSwarmStore(root).set_run_status(run_id, "paused"))
    child = ProjectSwarmStore(project_root).get_run(admission.run_id)
    assert child is not None and child.status == "paused"
    assert supervisor.reconcile_host_dispatch(project_root, admission.run_id, failure_reason="host_execution_returned") == "paused"
    assert supervisor.list_active_admissions()[0]["state"] == "paused"


def test_three_space_governance_gate_denies_nova_and_finanzjunkie_before_actions(
    tmp_path: Path,
) -> None:
    """Controller and ordinary Spaces cannot cross the Aquarium action gate."""
    nova_root = tmp_path / "nova"
    finance_root = tmp_path / "finanzjunkie"
    aquarium_root = tmp_path / "aquarium-zentrum"
    governance = {
        "nova": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex,
            canonical_root=nova_root,
            yolo=False,
            enrolled=True,
            revision=1,
            policy_identity="space-governance:nova",
        ),
        "finanzjunkie": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex,
            canonical_root=finance_root,
            yolo=True,
            enrolled=False,
            revision=1,
            policy_identity="space-governance:finanzjunkie",
        ),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex,
            canonical_root=aquarium_root,
            yolo=True,
            enrolled=True,
            revision=2,
            policy_identity="space-governance:aquarium",
        ),
    }
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=lambda target: governance.get(target),
    )
    for target in ("nova", "finanzjunkie"):
        rejected = supervisor.admit(target, {"kind": "external-or-irreversible"})
        assert rejected.status == "rejected"
        assert rejected.reason == "not_yolo_enrolled"
    admitted = supervisor.admit("aquarium-zentrum", {"kind": "github.push"})
    assert admitted.status == "created"
    assert admitted.capability is not None
    assert admitted.capability._target_key == "aquarium-zentrum"
    assert admitted.capability._allowed_action_families
    assert supervisor.list_active_admissions()[0]["target_space_id"] == governance["aquarium-zentrum"].space_id


def test_three_space_global_slot_is_exactly_once_across_enrolled_spaces(
    tmp_path: Path,
) -> None:
    """Two enrolled Spaces still serialize work through the one Nova slot.

    This closes the gap between per-Space admission tests and the host
    contract: a duplicate intent is coalesced, a different enrolled Space is
    rejected while the first run owns the global slot, and it can proceed only
    after the first run is durably completed.
    """
    roots = {
        "aquarium-zentrum": tmp_path / "aquarium-zentrum",
        "finanzjunkie": tmp_path / "finanzjunkie",
    }
    for root in roots.values():
        root.mkdir(parents=True)
    records = {
        target: ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex,
            canonical_root=root,
            yolo=True,
            enrolled=True,
            revision=1,
            policy_identity="space-governance:three-space-slot",
        )
        for target, root in roots.items()
    }
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=lambda target: records.get(target),
    )

    first = supervisor.admit(
        "aquarium-zentrum", {"goal": "repair aquarium", "kind": "maintenance"}
    )
    assert first.status == "created" and first.capability is not None

    # Exactly-once is keyed by the durable intent, not by a second dispatch.
    duplicate = supervisor.admit(
        "aquarium-zentrum", {"goal": "repair aquarium", "kind": "maintenance"}
    )
    assert duplicate.status == "coalesced"
    assert duplicate.run_id == first.run_id

    blocked_finance = supervisor.admit(
        "finanzjunkie", {"goal": "rebalance portfolio", "kind": "maintenance"}
    )
    assert blocked_finance.status == "rejected"
    assert blocked_finance.reason == "active_limit"

    store = ProjectSwarmStore(roots["aquarium-zentrum"])
    child = store.get_run(first.run_id)
    assert child is not None
    store.set_run_status(first.run_id, "running")
    store.set_run_status(first.run_id, "completed")
    assert supervisor.record_completion(first.run_id) is True
    assert supervisor.record_completion(first.run_id) is False

    next_finance = supervisor.admit(
        "finanzjunkie", {"goal": "rebalance portfolio", "kind": "maintenance"}
    )
    assert next_finance.status == "created"
    assert next_finance.run_id != first.run_id

    # Revocation is a host lifecycle boundary: an active run is paused before
    # the Space authority is changed, and its completion cannot release the
    # slot or be accepted as exactly-once completion afterwards.
    assert next_finance.capability is not None
    finance_store = ProjectSwarmStore(roots["finanzjunkie"])

    def revoke_during_dispatch(root: Path, run_id: str) -> None:
        assert root == roots["finanzjunkie"]
        assert finance_store.get_run(run_id).status == "running"
        assert supervisor.pause_for_space_change(
            "finanzjunkie",
            reason="governance_changed",
            actor=SYSTEM_SPACE_LIFECYCLE_ACTOR,
        ) is True
        assert finance_store.get_run(run_id).status == "paused"

    assert supervisor.start_admitted_run(
        next_finance.capability, dispatcher=revoke_during_dispatch
    ) is True
    assert supervisor.record_completion(next_finance.run_id) is False
    assert supervisor.list_active_admissions()[0]["state"] == "paused"

    # A later intent cannot bypass the revoked/paused generation, even though
    # the resolver still contains the old fixture until the caller persists
    # its governance change.
    duplicate_after_revoke = supervisor.admit(
        "finanzjunkie", {"goal": "rebalance portfolio", "kind": "maintenance"}
    )
    assert duplicate_after_revoke.status == "coalesced"
    assert duplicate_after_revoke.run_id == next_finance.run_id


def test_snapshot_three_space_exactly_once_and_revocation_fail_closed(tmp_path: Path) -> None:
    """Fake host coverage for identity/root/trust binding across product Spaces."""
    roots = {slug: tmp_path / "spaces" / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    governance = {
        "nova": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["nova"], root_fingerprint="",
            yolo=False, enrolled=False, revision=1, policy_identity="space:test",
        ),
        "finanzjunkie": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["finanzjunkie"], root_fingerprint="",
            yolo=True, enrolled=True, revision=1, policy_identity="space:test",
        ),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["aquarium-zentrum"], root_fingerprint="",
            yolo=True, enrolled=True, revision=1, policy_identity="space:test",
        ),
    }
    for root in roots.values():
        root.mkdir(parents=True)
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=governance.get,
    )
    dispatched: list[tuple[Path, str]] = []

    def fake_dispatch(root: Path, run_id: str) -> None:
        dispatched.append((root, run_id))
        # Keep Aquarium active so Finanz-junkie exercises the global
        # exactly-once slot and remains pending for the revocation check.
        if root.name != "aquarium-zentrum":
            ProjectSwarmStore(root).set_run_status(run_id, "completed")
            assert supervisor.record_completion(run_id) is True
            assert supervisor.record_completion(run_id) is False

    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=fake_dispatch,
        governance_snapshots=lambda: governance,
    )

    first = runtime.pulse(now_epoch=100.0)
    assert [(item.target_key, item.status) for item in first] == [
        ("aquarium-zentrum", "started"), ("finanzjunkie", "active_limit")
    ]
    assert len(dispatched) == 1
    assert dispatched[0][0] == roots["aquarium-zentrum"]
    assert len({item.space_id for item in governance.values()}) == 3
    assert all(item.root_fingerprint for item in governance.values())
    assert {item.canonical_root for item in governance.values()} == set(roots.values())

    # Revocation is checked against the live resolver before any retry. The
    # waiting finance intent remains pending, but cannot dispatch or inherit a
    # stale root/revision while enrollment is disabled.
    revoked = governance["finanzjunkie"]
    governance["finanzjunkie"] = replace(revoked, enrolled=False, revision=2)
    revoked_outcome = runtime.pulse(now_epoch=101.0)
    assert [(item.target_key, item.status) for item in revoked_outcome] == [
        ("finanzjunkie", "ineligible")
    ]
    assert len(dispatched) == 1
    assert runtime.status()[1].pending is True

    # Re-enrollment is a new governance revision. It may create a new
    # periodic intent later, but this check itself never resumes old work.
    governance["finanzjunkie"] = replace(revoked, revision=3)
    resumed_check = runtime.pulse(now_epoch=102.0)
    assert [(item.target_key, item.status) for item in resumed_check] == [("finanzjunkie", "active_limit")]
    assert len(dispatched) == 1