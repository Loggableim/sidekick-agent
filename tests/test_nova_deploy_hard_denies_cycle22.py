import json

from nova.managed_space_gateway import ManagedWorktreeHandle, TargetDeploymentRequest
from nova.production_workers import ProductionDeploymentWorker, ProductionDiagnosisRunner


def _handle(root, worktree):
    return ManagedWorktreeHandle(root, worktree, "run", "identity", "a" * 64)


def test_deploy_worker_rejects_secret_and_host_target_arguments(tmp_path, monkeypatch):
    root = tmp_path / "root"
    worktree = root / "worktree"
    (worktree / ".swarm").mkdir(parents=True)
    (worktree / ".swarm" / "deploy.json").write_text(
        json.dumps({"argv": ["python", "-c", "open('C:/secrets.json').read()"]}),
        encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr("nova.production_workers._run_argv", lambda *args, **kwargs: calls.append(args))
    result = ProductionDeploymentWorker().execute(_handle(root, worktree), TargetDeploymentRequest())
    assert result.code == "deployment_config_invalid"
    assert calls == []
    diagnosis = ProductionDiagnosisRunner(worktree).diagnose(None, 10)
    assert diagnosis.code == "deployment_config_invalid"
