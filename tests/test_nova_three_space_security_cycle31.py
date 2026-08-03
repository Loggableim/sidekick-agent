"""Security boundary regression for production-shaped three-Space actions."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from uuid import uuid4

from nova.managed_space_gateway import (
    GitHubPushRequest, GitHubReleaseRequest, ManagedWorktreeHandle, TargetDeploymentRequest,
)
from nova.production_workers import ProductionDeploymentWorker, ProductionGitHubWorker
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def test_three_space_github_deploy_hard_denies_are_audited_and_side_effect_free(tmp_path: Path, monkeypatch) -> None:
    roots = {slug: tmp_path / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    for root in roots.values():
        (root / ".swarm").mkdir(parents=True)
    governance = {
        "nova": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["nova"], yolo=False,
            enrolled=False, revision=1, policy_identity="cycle31",
        ),
        "finanzjunkie": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["finanzjunkie"], yolo=True,
            enrolled=True, revision=1, policy_identity="cycle31",
        ),
        "aquarium-zentrum": ManagedSpaceGovernance.from_values(
            space_id=uuid4().hex, canonical_root=roots["aquarium-zentrum"], yolo=True,
            enrolled=True, revision=1, policy_identity="cycle31",
        ),
    }
    assert ManagedSpaceSupervisor(
        ledger_path=tmp_path / "nova.sqlite", governance_resolver=governance.get,
    ).admit("nova", {"goal": "controller", "kind": "maintenance"}).reason == "not_yolo_enrolled"

    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("nova.production_workers._run_argv", lambda *args, **kwargs: calls.append(args))
    for target in ("finanzjunkie", "aquarium-zentrum"):
        supervisor = ManagedSpaceSupervisor(
            ledger_path=tmp_path / (target + ".sqlite"), governance_resolver=governance.get,
        )
        admission = supervisor.admit(target, {"goal": target, "kind": "maintenance"})
        assert admission.capability is not None
        allowed = admission.capability._allowed_action_families
        assert "github_publication" in allowed and "target_deployment_worker" in allowed
        assert not {"admin", "payments", "secrets", "credentials"}.intersection(allowed)
        with sqlite3.connect(tmp_path / (target + ".sqlite")) as db:
            assert db.execute("SELECT COUNT(*) FROM supervisor_audit WHERE admission_id = ?", (admission.admission_id,)).fetchone()[0] >= 1

        handle = ManagedWorktreeHandle(roots[target], roots[target], admission.run_id, "cycle31", "a" * 64)
        github = ProductionGitHubWorker()
        assert github.execute(handle, GitHubPushRequest("../main")).code == "operation_hard_denied"
        assert github.execute(handle, GitHubReleaseRequest("v1", "release", "token=secret")).code == "operation_hard_denied"
        deploy_config = roots[target] / ".swarm" / "deploy.json"
        deploy_config.write_text(json.dumps({"argv": ["python", "-c", "open('C:/secrets.json').read()"]}), encoding="utf-8")
        assert ProductionDeploymentWorker().execute(handle, TargetDeploymentRequest()).code == "deployment_config_invalid"
    assert calls == []

