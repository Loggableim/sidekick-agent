"""Durable Plan/Execute workflow state and runtime tool policy.

The workflow record intentionally lives beside a profile rather than inside
either Sidekick session store.  Chat sessions have different lifetimes and
formats on the CLI, gateway, and WebUI; a small session-keyed record keeps the
approval contract identical across all three surfaces.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Iterator

from shared.utils import atomic_json_write


WORKFLOW_PHASES = frozenset(
    {
        "idle",
        "drafting",
        "awaiting_approval",
        "revising",
        "executing",
        "completed",
        "rejected",
        "failed",
    }
)

# Plan Mode is intentionally deny-by-default.  Add a tool only after it has
# been assessed as observational for every argument shape, including browser
# and MCP-adjacent tools.
PLAN_READ_ONLY_TOOLS = frozenset(
    {
        "browser_snapshot",
        "browser_status",
        "get_current_time",
        "read_file",
        "search_files",
        "session_search",
        "skill_view",
        "skills_list",
        "web_fetch",
        "web_search",
    }
)

_workflow_mode: ContextVar[str] = ContextVar("sidekick_workflow_mode", default="action")
_workflow_session_id: ContextVar[str] = ContextVar("sidekick_workflow_session_id", default="")
_workflow_plan_id: ContextVar[str] = ContextVar("sidekick_workflow_plan_id", default="")


class WorkflowError(RuntimeError):
    """Base class for workflow state failures."""


class WorkflowNotFoundError(WorkflowError):
    """Raised when a requested session has no workflow record."""


class WorkflowConflictError(WorkflowError):
    """Raised for stale versions or incompatible state transitions."""


class WorkflowApprovalError(WorkflowError):
    """Raised when execution lacks an approval for the current plan version."""


class WorkflowLockError(WorkflowError):
    """Raised when a profile workflow lock cannot be acquired safely."""


_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _LOCKS[key] = lock
        return lock


class _WorkflowFileLock:
    """Small cross-process lock which needs no optional dependency."""

    def __init__(self, path: Path, timeout: float = 5.0):
        self.path = path
        self.timeout = timeout
        self._thread_lock = _lock_for(path)
        self._handle = None

    def __enter__(self):
        self._thread_lock.acquire()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a+b")
            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"0")
                self._handle.flush()
            self._handle.seek(0)
            deadline = time.monotonic() + self.timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:  # pragma: no cover - exercised on Linux installs
                        import fcntl

                        fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except OSError as exc:
                    if time.monotonic() >= deadline:
                        raise WorkflowLockError(f"Could not acquire workflow lock: {self.path}") from exc
                    time.sleep(0.025)
        except BaseException:
            if self._handle is not None:
                self._handle.close()
                self._handle = None
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc, tb):
        try:
            if self._handle is not None:
                self._handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - exercised on Linux installs
                    import fcntl

                    fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
                self._handle.close()
                self._handle = None
        finally:
            self._thread_lock.release()


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _session_filename(session_id: str) -> str:
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id is required")
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest() + ".json"


def _default_profile_home() -> Path:
    from runtime._compat.shim_constants import get_sidekick_home

    return Path(get_sidekick_home())


class WorkflowStore:
    """Versioned workflow records scoped to one active Sidekick profile."""

    def __init__(self, profile_home: Path | str | None = None):
        self.profile_home = Path(profile_home) if profile_home is not None else _default_profile_home()
        self.root = self.profile_home / "workflows"
        self.lock_root = self.root / ".locks"

    def _paths(self, session_id: str) -> tuple[Path, Path]:
        filename = _session_filename(session_id)
        return self.root / filename, self.lock_root / (filename + ".lock")

    @staticmethod
    def _load(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkflowError(f"Workflow state is unreadable: {path}") from exc
        if not isinstance(value, dict) or not value.get("plan_id"):
            raise WorkflowError(f"Workflow state is invalid: {path}")
        return value

    def _read_for_session(self, session_id: str) -> tuple[Path, Path, dict[str, Any] | None]:
        path, lock_path = self._paths(session_id)
        with _WorkflowFileLock(lock_path):
            state = self._load(path)
        return path, lock_path, copy.deepcopy(state) if state is not None else None

    @staticmethod
    def _assert_identity(state: dict[str, Any], plan_id: str) -> None:
        if state.get("plan_id") != plan_id:
            raise WorkflowNotFoundError("Plan does not belong to this session")

    @staticmethod
    def _assert_version(state: dict[str, Any], version: int) -> None:
        if not isinstance(version, int) or version != state.get("version"):
            raise WorkflowConflictError("Plan version is stale; reload the current workflow state")

    @staticmethod
    def _assert_phase(state: dict[str, Any], *allowed: str) -> None:
        if state.get("phase") not in allowed:
            raise WorkflowConflictError(
                f"Workflow is {state.get('phase', 'unknown')}; expected one of {', '.join(allowed)}"
            )

    @staticmethod
    def _touch(state: dict[str, Any]) -> dict[str, Any]:
        state["updated_at"] = _utc_timestamp()
        return state

    def _mutate(self, session_id: str, update) -> dict[str, Any]:
        path, lock_path = self._paths(session_id)
        with _WorkflowFileLock(lock_path):
            current = self._load(path)
            next_state = update(copy.deepcopy(current) if current is not None else None)
            if not isinstance(next_state, dict):
                raise WorkflowError("Workflow update did not produce a record")
            if next_state.get("phase") not in WORKFLOW_PHASES:
                raise WorkflowError(f"Invalid workflow phase: {next_state.get('phase')}")
            atomic_json_write(path, self._touch(next_state), sort_keys=True)
            return copy.deepcopy(next_state)

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        _, _, state = self._read_for_session(session_id)
        return state

    def create_plan(self, session_id: str, request: str, *, mode: str = "plan") -> dict[str, Any]:
        request = str(request or "").strip()
        if not request:
            raise ValueError("request is required")

        def create(existing):
            if existing and existing.get("phase") in {"drafting", "awaiting_approval", "revising", "executing"}:
                raise WorkflowConflictError("A workflow is already active for this session")
            now = _utc_timestamp()
            return {
                "schema_version": 1,
                "session_id": session_id,
                "plan_id": uuid.uuid4().hex,
                "mode": mode,
                "phase": "drafting",
                "version": 1,
                "request": request,
                "plan_markdown": "",
                "created_at": now,
                "updated_at": now,
                "approved_at": None,
                "approver": None,
                "approval_version": None,
                "execution_stream_id": None,
                "completed_at": None,
                "failed_at": None,
            }

        return self._mutate(session_id, create)

    def record_plan(self, session_id: str, plan_id: str, version: int, plan_markdown: str) -> dict[str, Any]:
        plan_markdown = str(plan_markdown or "").strip()
        if not plan_markdown:
            raise ValueError("plan_markdown is required")

        def record(existing):
            if existing is None:
                raise WorkflowNotFoundError("No workflow exists for this session")
            self._assert_identity(existing, plan_id)
            self._assert_version(existing, version)
            self._assert_phase(existing, "drafting", "revising")
            existing.update({"mode": "plan", "phase": "awaiting_approval", "plan_markdown": plan_markdown})
            return existing

        return self._mutate(session_id, record)

    def revise(self, session_id: str, plan_id: str, version: int, feedback: str) -> dict[str, Any]:
        feedback = str(feedback or "").strip()
        if not feedback:
            raise ValueError("feedback is required")

        def revise(existing):
            if existing is None:
                raise WorkflowNotFoundError("No workflow exists for this session")
            self._assert_identity(existing, plan_id)
            self._assert_version(existing, version)
            self._assert_phase(existing, "awaiting_approval")
            existing.update(
                {
                    "mode": "plan",
                    "phase": "drafting",
                    "version": int(existing["version"]) + 1,
                    "request": f"{existing['request']}\n\nRevision feedback:\n{feedback}",
                    "plan_markdown": "",
                    "approved_at": None,
                    "approver": None,
                    "approval_version": None,
                    "execution_stream_id": None,
                }
            )
            return existing

        return self._mutate(session_id, revise)

    def approve(self, session_id: str, plan_id: str, version: int, *, approver: str) -> dict[str, Any]:
        approver = str(approver or "").strip()
        if not approver:
            raise ValueError("approver is required")

        def approve(existing):
            if existing is None:
                raise WorkflowNotFoundError("No workflow exists for this session")
            self._assert_identity(existing, plan_id)
            self._assert_version(existing, version)
            self._assert_phase(existing, "awaiting_approval")
            existing.update(
                {
                    "approved_at": _utc_timestamp(),
                    "approver": approver,
                    "approval_version": existing["version"],
                }
            )
            return existing

        return self._mutate(session_id, approve)

    def reject(self, session_id: str, plan_id: str, version: int, *, reason: str = "") -> dict[str, Any]:
        def reject(existing):
            if existing is None:
                raise WorkflowNotFoundError("No workflow exists for this session")
            self._assert_identity(existing, plan_id)
            self._assert_version(existing, version)
            self._assert_phase(existing, "awaiting_approval")
            existing.update({"phase": "rejected", "rejection_reason": str(reason or "").strip()})
            return existing

        return self._mutate(session_id, reject)

    def begin_execution(self, session_id: str, plan_id: str, version: int, *, stream_id: str) -> dict[str, Any]:
        stream_id = str(stream_id or "").strip()
        if not stream_id:
            raise ValueError("stream_id is required")

        def execute(existing):
            if existing is None:
                raise WorkflowNotFoundError("No workflow exists for this session")
            self._assert_identity(existing, plan_id)
            self._assert_version(existing, version)
            self._assert_phase(existing, "awaiting_approval")
            if existing.get("approval_version") != existing.get("version"):
                raise WorkflowApprovalError("The current plan version has not been explicitly approved")
            existing.update({"mode": "execute", "phase": "executing", "execution_stream_id": stream_id})
            return existing

        return self._mutate(session_id, execute)

    def assert_execution_authorized(
        self,
        session_id: str,
        plan_id: str,
        version: int,
        *,
        stream_id: str,
    ) -> dict[str, Any]:
        """Return the durable state only for the exact authorized execution turn."""
        stream_id = str(stream_id or "").strip()
        if not stream_id:
            raise WorkflowApprovalError("Execution requires the server-issued workflow stream")
        state = self.get_session(session_id)
        if state is None:
            raise WorkflowNotFoundError("No workflow exists for this session")
        self._assert_identity(state, plan_id)
        self._assert_version(state, version)
        if state.get("phase") != "executing":
            raise WorkflowApprovalError("The current plan is not in an authorized execution state")
        if state.get("approval_version") != state.get("version"):
            raise WorkflowApprovalError("The current plan version has not been explicitly approved")
        if state.get("execution_stream_id") != stream_id:
            raise WorkflowApprovalError("Execution is not associated with the server-issued workflow stream")
        return state

    def fail(self, session_id: str, plan_id: str, *, reason: str = "") -> dict[str, Any]:
        """Persist a failed planner/execution transition without reopening approval."""
        def fail(existing):
            if existing is None:
                raise WorkflowNotFoundError("No workflow exists for this session")
            self._assert_identity(existing, plan_id)
            self._assert_phase(existing, "drafting", "revising", "awaiting_approval", "executing")
            existing.update(
                {
                    "phase": "failed",
                    "failed_at": _utc_timestamp(),
                    "failure_reason": str(reason or "").strip(),
                }
            )
            return existing

        return self._mutate(session_id, fail)

    def finish_execution(self, session_id: str, plan_id: str, *, failed: bool = False) -> dict[str, Any]:
        def finish(existing):
            if existing is None:
                raise WorkflowNotFoundError("No workflow exists for this session")
            self._assert_identity(existing, plan_id)
            self._assert_phase(existing, "executing")
            existing["phase"] = "failed" if failed else "completed"
            existing["failed_at" if failed else "completed_at"] = _utc_timestamp()
            return existing

        return self._mutate(session_id, finish)


@contextmanager
def workflow_context(*, mode: str, session_id: str = "", plan_id: str = "") -> Iterator[None]:
    """Apply workflow mode to all tool handlers in the current execution context."""
    mode_token = _workflow_mode.set(str(mode or "action").strip().lower() or "action")
    session_token = _workflow_session_id.set(str(session_id or ""))
    plan_token = _workflow_plan_id.set(str(plan_id or ""))
    try:
        yield
    finally:
        _workflow_plan_id.reset(plan_token)
        _workflow_session_id.reset(session_token)
        _workflow_mode.reset(mode_token)


def current_workflow_mode() -> str:
    return _workflow_mode.get()


def plan_tool_block_reason(tool_name: str) -> str | None:
    """Return a fail-closed denial string for a non-observational Plan Mode tool."""
    if current_workflow_mode() != "plan":
        return None
    normalized = str(tool_name or "").strip().lower()
    if normalized in PLAN_READ_ONLY_TOOLS:
        return None
    return (
        f"Plan mode blocks '{normalized or 'unknown'}': only read-only inspection tools are allowed "
        "until the current plan is explicitly approved and execution is started."
    )
