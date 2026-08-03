from __future__ import annotations

import json

import pytest
from pathlib import Path
from types import SimpleNamespace

from nova.managed_space_gateway import (
    GitHubPushRequest,
    LocalWriteFileRequest,
    ManagedWorktreeHandle,
    TargetDeploymentRequest,
    _build_request,
)
from nova.production_workers import (
    ProductionDeploymentWorker,
    ProductionDiagnosisRunner,
    ProductionManagedActionExecutor,
    ProductionGitHubWorker,
    ProductionLocalWorker,
    _safe_env,
)


def _handle(root: Path, worktree: Path) -> ManagedWorktreeHandle:
    return ManagedWorktreeHandle(root.resolve(), worktree.resolve(), "run-1", "wt:1", "a" * 64)


def test_safe_env_rejects_compound_credential_names(monkeypatch) -> None:
    monkeypatch.setenv("SERVICE_APIKEY", "secret")
    monkeypatch.setenv("VENDOR_AUTHORIZATION", "bearer")
    monkeypatch.setenv("NORMAL_RUNTIME_FLAG", "1")

    env = _safe_env()

    assert "SERVICE_APIKEY" not in env
    assert "VENDOR_AUTHORIZATION" not in env
    assert env["NORMAL_RUNTIME_FLAG"] == "1"


def test_local_worker_rejects_write_outside_attested_worktree(tmp_path: Path) -> None:
    root = tmp_path / "root"
    worktree = root / "worktree"
    worktree.mkdir(parents=True)
    result = ProductionLocalWorker().execute(
        _handle(root, worktree),
        LocalWriteFileRequest("../escape.txt", "nope"),
    )
    assert result.ok is False
    assert not (tmp_path / "escape.txt").exists()


def test_github_worker_push_uses_fixed_origin_and_no_shell(monkeypatch, tmp_path: Path) -> None:
    calls: list[dict] = []

    class Completed:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        return Completed()

    monkeypatch.setattr("nova.production_workers.subprocess.run", fake_run)
    result = ProductionGitHubWorker().execute(
        _handle(tmp_path, tmp_path),
        GitHubPushRequest("main"),
    )
    assert result.ok is True
    assert calls[0]["argv"][-3:] == ["push", "origin", "main"]
    assert calls[0]["shell"] is False


def test_deployment_worker_rejects_redirected_config(tmp_path: Path) -> None:
    root = tmp_path / "root"
    worktree = root / "worktree"
    swarm = worktree / ".swarm"
    swarm.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({"argv": ["python", "-c", "print(1)"]}), encoding="utf-8")
    try:
        (swarm / "deploy.json").symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink unavailable: {exc}")
    result = ProductionDeploymentWorker().execute(_handle(root, worktree), TargetDeploymentRequest())
    assert result.ok is False
    assert result.code == "deployment_config_invalid"


def test_deployment_worker_requires_explicit_safe_argv(tmp_path: Path) -> None:
    root = tmp_path / "root"
    worktree = root / "worktree"
    (worktree / ".swarm").mkdir(parents=True)
    (worktree / ".swarm" / "deploy.json").write_text(
        json.dumps({"argv": ["powershell", "-Command", "Remove-Item *"]}),
        encoding="utf-8",
    )
    result = ProductionDeploymentWorker().execute(
        _handle(root, worktree),
        TargetDeploymentRequest(),
    )
    assert result.ok is False
    assert result.code == "deployment_config_invalid"


def test_deployment_diagnosis_reports_read_only_preflight_without_retry(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / ".swarm").mkdir(parents=True)
    (root / ".swarm" / "deploy.json").write_text(
        json.dumps({"argv": ["python", "-c", "print('deploy')"]}),
        encoding="utf-8",
    )

    result = ProductionDiagnosisRunner(root).diagnose(None, 127)

    assert result.restoration_verified is False
    assert result.code == "deployment_preflight_valid"


def test_deployment_diagnosis_stops_when_budget_is_exhausted(tmp_path: Path) -> None:
    result = ProductionDiagnosisRunner(tmp_path).diagnose(None, 0)

    assert result.restoration_verified is False
    assert result.code == "deployment_budget_exhausted"


def test_gateway_rejects_credential_like_local_paths_before_worker() -> None:
    assert _build_request(
        "local.write_file",
        {"artifact_digest": "a" * 64, "path": ".env", "content": "TOKEN=x"},
    ) is None
    assert _build_request(
        "local.apply_patch",
        {"artifact_digest": "a" * 64, "path": "certs/private.pem", "patch": "diff"},
    ) is None


def test_managed_action_executor_pauses_when_project_store_disappears(
    monkeypatch, tmp_path: Path
) -> None:
    """A cleanup race must return a bounded result, never escape the worker thread."""
    root = tmp_path / "missing-project"
    capability = object()
    context = object()
    pauses: list[tuple[object, object, str]] = []

    class Supervisor:
        def _binding_for_run(self, _root, _run):
            return capability

        def resolve_action_context(self, supplied):
            assert supplied is capability
            return context

        def pause_action_context(self, supplied, supplied_context, reason):
            pauses.append((supplied, supplied_context, reason))
            return True

    def missing_store(_root):
        raise FileNotFoundError("project removed during cleanup")

    monkeypatch.setattr("swarm_core.store.ProjectSwarmStore", missing_store)
    result = ProductionManagedActionExecutor(Supervisor()).execute(
        object(), SimpleNamespace(metadata={"project_root": str(root)})
    )

    assert result.ok is False
    assert result.code == "project_store_unavailable"
    assert pauses == [(capability, context, "project_store_unavailable")]

def test_managed_action_executor_fail_closes_when_cleanup_revokes_after_worker():
    """A worker success is not surfaced after a concurrent capability cleanup."""
    from nova.managed_space_gateway import GatewayResult
    from nova.production_workers import ProductionManagedActionExecutor

    capability = object()
    context = object()
    pauses = []

    class Supervisor:
        revoked = False

        def resolve_action_context(self, supplied):
            assert supplied is capability
            return None if self.revoked else context

        def pause_action_context(self, supplied, supplied_context, reason):
            pauses.append((supplied, supplied_context, reason))

    supervisor = Supervisor()
    executor = ProductionManagedActionExecutor.__new__(ProductionManagedActionExecutor)
    executor._supervisor = supervisor
    result = GatewayResult(True, "local_completed")
    supervisor.revoked = True

    reconciled = executor._reconcile_worker_result(
        capability, context, result, GatewayResult
    )

    assert reconciled.ok is False
    assert reconciled.code == "capability_invalid"
    assert pauses == [(capability, context, "capability_invalid")]