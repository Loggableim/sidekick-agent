"""Host-owned workers for the managed YOLO action gateway.

The workers receive only an attested worktree handle. They never receive a
capability, a secret value, or an arbitrary shell string. GitHub credentials
remain in the host credential helper; deployment commands are argv-only and
must be explicitly declared by the target Space.
"""

from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

from nova.managed_space_gateway import (
    DiagnosisResult,
    GitHubCommitRequest,
    GitHubPullRequestRequest,
    GitHubPushRequest,
    GitHubReleaseRequest,
    LocalApplyPatchRequest,
    LocalFormatRequest,
    LocalTestRequest,
    LocalWriteFileRequest,
    ManagedRequest,
    ManagedSpaceActionGateway,
    ManagedWorktreeHandle,
    TargetDeploymentRequest,
    WorkerResult,
    CreatedWorktree,
    GitWorktreeAttestor,
    _protected_relative_path,
    _safe_ref,
    _bounded_text,
    _contains_sensitive_value,
)
from runtime._compat.shim_constants import get_sidekick_home

_SAFE_EXECUTABLES = frozenset(
    {"npm", "npm.cmd", "pnpm", "pnpm.cmd", "yarn", "yarn.cmd", "python", "python.exe", "uv", "uv.exe", "cargo", "dotnet", "make"}
)
_SECRET_ENV_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "AUTHORIZATION", "PRIVATE", "BEARER")
_SHELL_MARKERS = re.compile(r"[;&|<>`$\r\n]")
# Deployment argv remains project-scoped, but may never name host secrets or
# privileged/destructive targets even when a safe executable is selected.
_DEPLOY_FORBIDDEN_MARKERS = re.compile(
    r"(?i)(?:\\.env(?:\\.|$)|credentials?|secrets?|private[_-]?key|id_rsa|"
    r"password|token|api[_-]?key|authorization|/etc/|/proc/|system32|"
    r"sudo|admin|delete|destroy|remove|payment)"
)
_DEPLOY_FORBIDDEN_PATH_PARTS = (".env", "credential", "secret", "private_key", "id_rsa", "password", "token", "api_key", "authorization", "passwd", "auth.json")

def _deployment_argument_forbidden(value: str) -> bool:
    normalized = value.casefold()
    return bool(_DEPLOY_FORBIDDEN_MARKERS.search(value)) or any(
        part in normalized for part in _DEPLOY_FORBIDDEN_PATH_PARTS
    )

_MAX_OUTPUT = 2_000


def _safe_env() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in _SECRET_ENV_MARKERS)
    }


def _safe_deploy_config_path(base: Path) -> Path | None:
    """Resolve deploy.json only inside the attested project root.

    Missing configuration is distinct from an invalid symlink/junction so callers
    can report a useful fail-closed code without ever reading outside the root.
    """
    root = Path(base).resolve(strict=True)
    swarm_dir = root / ".swarm"
    if not swarm_dir.exists():
        return None
    if swarm_dir.is_symlink() or getattr(swarm_dir, "is_junction", lambda: False)():
        raise ValueError("deployment config directory is redirected")
    config_path = swarm_dir / "deploy.json"
    if not config_path.exists():
        return None
    if config_path.is_symlink() or getattr(config_path, "is_junction", lambda: False)():
        raise ValueError("deployment config is redirected")
    resolved = config_path.resolve(strict=True)
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise ValueError("deployment config escapes project root")
    return resolved


def _run_argv(argv: list[str], cwd: Path, *, timeout: int = 120) -> WorkerResult:
    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd),
            env=_safe_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError):
        return WorkerResult(False, "worker_failed")
    if completed.returncode != 0:
        return WorkerResult(False, "worker_failed")
    return WorkerResult(True, "worker_completed")


def _worktree_path(root: Path, run_id: str) -> Path:
    fingerprint = sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:32]
    return (
        Path(get_sidekick_home())
        / "state" / "nova-worktrees" / fingerprint / run_id
    ).resolve()


class ProductionWorktreeProvider:
    """Create one deterministic detached worktree outside the project tree."""

    def resolve(self, canonical_root: Path, run_id: str) -> Path:
        return _worktree_path(Path(canonical_root).resolve(), run_id)

    def create(self, canonical_root: Path, run_id: str) -> CreatedWorktree:
        root = Path(canonical_root).resolve(strict=True)
        target = self.resolve(root, run_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            check = subprocess.run(
                ["git", "-C", str(target), "rev-parse", "--show-toplevel"],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if check.returncode == 0 and Path(os.fsdecode(check.stdout).strip()).resolve() == target:
                return CreatedWorktree(target)
            raise RuntimeError("managed worktree path is occupied")
        completed = subprocess.run(
            ["git", "-C", str(root), "worktree", "add", "--detach", str(target), "HEAD"],
            capture_output=True,
            timeout=60,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("managed worktree creation failed")
        return CreatedWorktree(target)


class ProductionLocalWorker:
    def execute(self, handle: ManagedWorktreeHandle, request: ManagedRequest) -> WorkerResult:
        if isinstance(request, LocalApplyPatchRequest):
            try:
                result = subprocess.run(
                    ["git", "-C", str(handle.path), "apply", "--whitespace=nowarn", "-"],
                    input=request.patch,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
                return WorkerResult(result.returncode == 0, "local_completed" if result.returncode == 0 else "worker_failed")
            except (OSError, subprocess.SubprocessError):
                return WorkerResult(False, "worker_failed")
        if isinstance(request, LocalWriteFileRequest):
            target = (handle.path / request.path).resolve()
            if not target.is_relative_to(handle.path):
                return WorkerResult(False, "target_root_mismatch")
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(request.content, encoding="utf-8")
            except OSError:
                return WorkerResult(False, "worker_failed")
            return WorkerResult(True, "local_completed")
        if isinstance(request, LocalTestRequest):
            selector = request.path + "".join(f"::{node}" for node in request.nodes)
            return _run_argv([os.fspath(sys.executable), "-m", "pytest", selector], handle.path, timeout=300)
        if isinstance(request, LocalFormatRequest):
            return WorkerResult(False, "format_worker_unavailable")
        return WorkerResult(False, "operation_hard_denied")


class ProductionGitHubWorker:
    def execute(self, handle: ManagedWorktreeHandle, request: ManagedRequest) -> WorkerResult:
        if isinstance(request, GitHubCommitRequest):
            if _bounded_text(request.message, 512) is None or _contains_sensitive_value(request.message):
                return WorkerResult(False, "operation_hard_denied")
            staged = _run_argv(["git", "-C", str(handle.path), "add", "-A"], handle.path)
            if not staged.ok:
                return WorkerResult(False, "worker_failed")
            try:
                changed = subprocess.run(
                    ["git", "-C", str(handle.path), "diff", "--cached", "--name-only"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=30,
                    check=False,
                )
                paths = tuple(line.strip() for line in (changed.stdout or "").splitlines() if line.strip())
            except (OSError, subprocess.SubprocessError):
                return WorkerResult(False, "worker_failed")
            if changed.returncode != 0 or any(_protected_relative_path(path) for path in paths):
                _run_argv(["git", "-C", str(handle.path), "reset"], handle.path)
                return WorkerResult(False, "protected_path_denied")
            return _run_argv(["git", "-C", str(handle.path), "commit", "-m", request.message], handle.path)
        if isinstance(request, GitHubPushRequest):
            if _safe_ref(request.branch) is None:
                return WorkerResult(False, "operation_hard_denied")
            return _run_argv(["git", "-C", str(handle.path), "push", "origin", request.branch], handle.path)
        if isinstance(request, GitHubPullRequestRequest):
            if (_bounded_text(request.title, 256) is None or _bounded_text(request.body, 32000, allow_empty=True) is None or _contains_sensitive_value({"title": request.title, "body": request.body}) or type(request.draft) is not bool):
                return WorkerResult(False, "operation_hard_denied")
            argv = ["gh", "pr", "create", "--title", request.title, "--body", request.body]
            if request.draft:
                argv.append("--draft")
            return _run_argv(argv, handle.path)
        if isinstance(request, GitHubReleaseRequest):
            if (_safe_ref(request.tag) is None or _bounded_text(request.title, 256) is None or _bounded_text(request.notes, 32000, allow_empty=True) is None or _contains_sensitive_value({"tag": request.tag, "title": request.title, "notes": request.notes})):
                return WorkerResult(False, "operation_hard_denied")
            return _run_argv(["gh", "release", "create", request.tag, "--title", request.title, "--notes", request.notes], handle.path)
        return WorkerResult(False, "operation_hard_denied")


class ProductionDeploymentWorker:
    def execute(self, handle: ManagedWorktreeHandle, request: ManagedRequest) -> WorkerResult:
        if not isinstance(request, TargetDeploymentRequest):
            return WorkerResult(False, "operation_hard_denied")
        try:
            config_path = _safe_deploy_config_path(handle.path)
        except ValueError:
            return WorkerResult(False, "deployment_config_invalid")
        if config_path is None:
            return WorkerResult(False, "deployment_not_configured")
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return WorkerResult(False, "deployment_config_invalid")
        argv = raw.get("argv") if isinstance(raw, dict) else None
        if (
            not isinstance(argv, list)
            or not 1 <= len(argv) <= 32
            or any(
                not isinstance(item, str)
                or not item
                or len(item) > 512
                or _SHELL_MARKERS.search(item)
                or _deployment_argument_forbidden(item)
                for item in argv
            )
            or Path(argv[0]).name.lower() not in _SAFE_EXECUTABLES
        ):
            return WorkerResult(False, "deployment_config_invalid")
        return _run_argv(argv, handle.path, timeout=900)


class ProductionDiagnosisRunner:
    """Perform a bounded, read-only deployment preflight after failure."""

    def __init__(self, project_root: Path) -> None:
        self._project_root = Path(project_root).resolve()

    def diagnose(self, _failure: Any, _remaining_budget: int) -> DiagnosisResult:
        try:
            remaining = int(_remaining_budget)
        except (TypeError, ValueError):
            remaining = 0
        if remaining <= 0:
            return DiagnosisResult(False, "deployment_budget_exhausted")
        try:
            config_path = _safe_deploy_config_path(self._project_root)
        except ValueError:
            return DiagnosisResult(False, "deployment_config_invalid")
        if config_path is None:
            return DiagnosisResult(False, "deployment_config_unreadable")
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return DiagnosisResult(False, "deployment_config_unreadable")
        argv = raw.get("argv") if isinstance(raw, dict) else None
        if (
            not isinstance(argv, list)
            or not 1 <= len(argv) <= 32
            or any(
                not isinstance(item, str)
                or not item
                or len(item) > 512
                or _SHELL_MARKERS.search(item)
                or _deployment_argument_forbidden(item)
                for item in argv
            )
            or Path(argv[0]).name.lower() not in _SAFE_EXECUTABLES
        ):
            return DiagnosisResult(False, "deployment_config_invalid")
        executable = shutil.which(Path(argv[0]).name)
        if not executable:
            return DiagnosisResult(False, "deployment_executable_missing")
        # The preflight deliberately does not execute the deployment command.
        # A syntactically valid command is not evidence of restoration.
        return DiagnosisResult(False, "deployment_preflight_valid")


class ProductionManagedActionExecutor:
    """Build the gateway only at action time from the current supervisor."""

    def __init__(self, supervisor: Any) -> None:
        self._supervisor = supervisor
        self._worktrees = ProductionWorktreeProvider()

    def execute(self, proposal: Any, run: Any):
        from swarm_core.policy import PolicyGate
        from swarm_core.store import ProjectSwarmStore

        from nova.managed_space_gateway import GatewayResult

        try:
            root = Path(run.metadata.get("project_root", "")).resolve()
            capability = self._supervisor._binding_for_run(root, run)
            context = (
                self._supervisor.resolve_action_context(capability)
                if capability is not None
                else None
            )
        except Exception:
            # A malformed or concurrently cleaned-up run is not authority.
            return GatewayResult(False, "capability_invalid")
        if capability is None or context is None:
            return GatewayResult(False, "capability_invalid")
        try:
            policy_store = ProjectSwarmStore(root)
            gateway = ManagedSpaceActionGateway(
                supervisor=self._supervisor,
                policy_gate=PolicyGate(policy_store),
                worktree_provider=self._worktrees,
                worktree_attestor=GitWorktreeAttestor(run_target_resolver=self._worktrees.resolve),
                local_worker=ProductionLocalWorker(),
                github_worker=ProductionGitHubWorker(),
                deployment_worker=ProductionDeploymentWorker(),
                diagnosis_runner=ProductionDiagnosisRunner(root),
            )
        except Exception as exc:
            # Store/config cleanup can race a worker. Pause through the
            # ledger-bound context without retaining or logging raw details.
            try:
                self._supervisor.pause_action_context(
                    capability,
                    context,
                    "project_store_unavailable",
                )
            except Exception:
                pass
            return GatewayResult(False, "project_store_unavailable")
        result = gateway.execute(capability, proposal)
        return self._reconcile_worker_result(capability, context, result, GatewayResult)

    def _reconcile_worker_result(self, capability: Any, context: Any, result: Any, result_factory: Any) -> Any:
        """Fail closed when cleanup revokes a capability during a worker call."""
        if getattr(result, "ok", False) is True:
            try:
                current = self._supervisor.resolve_action_context(capability)
            except Exception:
                current = None
            if current != context:
                try:
                    self._supervisor.pause_action_context(
                        capability, context, "capability_invalid"
                    )
                except Exception:
                    pass
                return result_factory(False, "capability_invalid")
        return result
