"""Final production worker effect-path audit for all fixed Spaces."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from nova.managed_space_gateway import GitHubCommitRequest, ManagedWorktreeHandle, TargetDeploymentRequest
from nova.production_workers import ProductionDeploymentWorker, ProductionGitHubWorker
from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor


def test_three_space_commit_secret_and_deploy_admin_effects_never_reach_subprocess(tmp_path: Path, monkeypatch) -> None:
    roots = {slug: tmp_path / slug for slug in ("nova", "finanzjunkie", "aquarium-zentrum")}
    for root in roots.values(): (root / ".swarm").mkdir(parents=True)
    governance = {
        slug: ManagedSpaceGovernance.from_values(space_id=uuid4().hex, canonical_root=root, yolo=slug != "nova", enrolled=slug != "nova", revision=1, policy_identity="cycle38")
        for slug, root in roots.items()
    }
    assert ManagedSpaceSupervisor(ledger_path=tmp_path / "nova.sqlite", governance_resolver=governance.get).admit("nova", {"goal": "controller", "kind": "maintenance"}).reason == "not_yolo_enrolled"
    calls: list[object] = []
    monkeypatch.setattr("nova.production_workers._run_argv", lambda *args, **kwargs: calls.append(args))
    for target in ("finanzjunkie", "aquarium-zentrum"):
        supervisor = ManagedSpaceSupervisor(ledger_path=tmp_path / (target + ".sqlite"), governance_resolver=governance.get)
        admission = supervisor.admit(target, {"goal": target, "kind": "maintenance"})
        assert admission.capability is not None
        handle = ManagedWorktreeHandle(roots[target], roots[target], admission.run_id, "cycle38", "a" * 64)
        assert ProductionGitHubWorker().execute(handle, GitHubCommitRequest("release token=secret")).code == "operation_hard_denied"
        (roots[target] / ".swarm" / "deploy.json").write_text(json.dumps({"argv": ["sudo", "deploy", "--admin"]}), encoding="utf-8")
        assert ProductionDeploymentWorker().execute(handle, TargetDeploymentRequest()).code == "deployment_config_invalid"
    assert calls == []
