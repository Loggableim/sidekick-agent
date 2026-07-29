"""Sidekick-owned bindings for project-local Swarm Core.

This module is deliberately outside ``swarm_core``: it is the only place that
knows how to call Sidekick's auxiliary LLM path, resolve provider capacity, and
derive a host-side human identity.  It never executes a proposed tool action.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, replace
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, ContextManager, Iterator, Mapping
from urllib.parse import urlsplit
from uuid import uuid4

from swarm_core.engine import PreCompletionHook, RunSummary, SwarmEngine
from swarm_core.models import (
    ModelCatalogSnapshot,
    ModelRegistry,
    OLLAMA_CLOUD_PROVIDER,
)
from swarm_core.packs import PackRegistry
from swarm_core.policy import proposal_digest
from swarm_core.store import ProjectSwarmStore
from swarm_core.transport import ModelProviderError, OllamaCloudTransport
from swarm_core.types import (
    ActionCapabilities,
    ActionProposal,
    ApprovalRecord,
    RequestedToolAction,
    SwarmRun,
)
from swarm_core.verifier import ReadOnlyVerifier


CatalogRefresher = Callable[[], ModelCatalogSnapshot]
ProviderSlot = Callable[[str, str], ContextManager[None]]
ActionClassifier = Callable[[RequestedToolAction], ActionCapabilities]
PauseObserver = Callable[[], None]
RunCompletionObserver = Callable[[Path, SwarmRun], None]


@dataclass(frozen=True)
class SwarmExecutionOptions:
    """Host-resolved limits and read-only lifecycle extensions for one run."""

    max_calls: int = 48
    max_concurrent: int = 3
    verifier: ReadOnlyVerifier | None = None
    pre_completion_hook: PreCompletionHook | None = None
    required_pre_completion_hook_id: str | None = None
    on_completed: RunCompletionObserver | None = None
    blocked_reason: str | None = None


ExecutionOptionsResolver = Callable[[Path, SwarmRun], SwarmExecutionOptions | None]

OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE = "ollama-cloud-api-live-verified"
OLLAMA_CLOUD_UNAVAILABLE_CATALOG_SOURCE = "ollama-cloud-api-live-unavailable"
OLLAMA_CLOUD_CANONICAL_BASE_URL = "https://ollama.com/v1"

_ALLOWED_BLOCKED_EXECUTION_OPTION_REASONS = frozenset(
    {
        "execution_options_blocked",
        "execution_options_unavailable",
        "invalid_execution_options",
        "nova_bridge_disabled",
        "nova_bridge_unavailable",
    }
)

_FALLBACK_SLOT_LOCK = threading.Lock()
_FALLBACK_SLOTS: dict[int, threading.BoundedSemaphore] = {}


def get_cli_host_actor() -> str:
    """Derive a local human principal from the operating-system identity.

    This intentionally accepts no CLI argument and does not trust environment
    variables such as ``USERNAME``.  Windows asks the current process token;
    POSIX records the numeric effective UID, which remains stable even when a
    display-name lookup is altered.
    """
    if os.name == "nt":
        return _windows_token_actor()
    getuid = getattr(os, "getuid", None)
    if not callable(getuid):
        raise RuntimeError("Cannot derive an operating-system CLI principal")
    return f"os:uid:{int(getuid())}"


def _windows_token_actor() -> str:
    """Read the current Windows token username through the native API."""
    try:
        import ctypes
        from ctypes import wintypes

        size = wintypes.DWORD(0)
        ctypes.windll.advapi32.GetUserNameW(None, ctypes.byref(size))
        if size.value < 2:
            raise OSError("GetUserNameW did not return a token username size")
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.advapi32.GetUserNameW(buffer, ctypes.byref(size)):
            raise OSError("GetUserNameW failed for the current process token")
        username = buffer.value.strip()
    except Exception as exc:
        raise RuntimeError("Cannot derive a Windows token CLI principal") from exc
    if not username:
        raise RuntimeError("Cannot derive a Windows token CLI principal")
    return f"os:windows-token-user:{username}"


class SidekickSwarmService:
    """Host service for explicit Swarm writes and strictly bounded model calls."""

    def __init__(
        self,
        *,
        call_llm: Callable[..., Any] | None = None,
        catalog_refresher: CatalogRefresher | None = None,
        provider_slot: ProviderSlot | None = None,
        action_classifier: ActionClassifier | None = None,
        execution_options_resolver: ExecutionOptionsResolver | None = None,
        pause_poll_seconds: float = 0.05,
    ) -> None:
        # Constructor purity matters: GET route handlers may construct a
        # service for dependency injection, but must never create `.swarm`.
        self._call_llm = call_llm or _sidekick_call_llm
        self._catalog_refresher = catalog_refresher or _refresh_ollama_catalog
        self._provider_slot = provider_slot or _sidekick_ollama_provider_slot
        self._action_classifier = action_classifier or _conservative_classifier
        self._execution_options_resolver = execution_options_resolver
        if pause_poll_seconds <= 0:
            raise ValueError("pause_poll_seconds must be positive")
        self._pause_poll_seconds = float(pause_poll_seconds)

    def refresh_models(self, project_root: Path) -> ModelCatalogSnapshot:
        """Perform the only catalog refresh path and persist its safe snapshot."""
        project_root = Path(project_root).resolve()
        snapshot = self._catalog_refresher()
        if snapshot.provider != OLLAMA_CLOUD_PROVIDER:
            raise ValueError("Swarm only accepts an ollama-cloud catalog snapshot")
        ProjectSwarmStore(project_root).save_model_catalog_snapshot(snapshot)
        return snapshot

    def run(
        self,
        goal: str,
        project_root: Path,
        *,
        pack: str = "coding-team",
        autonomy: str | None = None,
        host_metadata: Mapping[str, Any] | None = None,
    ) -> RunSummary:
        """Synchronously run the same durable lifecycle used by the CLI."""
        run = self.start_run(
            goal,
            project_root,
            pack=pack,
            autonomy=autonomy,
            host_metadata=host_metadata,
        )
        return self.execute_run(project_root, run.run_id)

    def start_run(
        self,
        goal: str,
        project_root: Path,
        *,
        pack: str = "coding-team",
        autonomy: str | None = None,
        host_metadata: Mapping[str, Any] | None = None,
    ) -> SwarmRun:
        """Persist a running Swarm identity without starting a model call."""
        project_root = Path(project_root).resolve()
        engine, _snapshot, _store = self._engine_for(project_root)
        return engine.start_run(
            goal,
            project_root,
            pack=pack,
            autonomy=autonomy,
            host_metadata=host_metadata,
        )

    def execute_run(
        self,
        project_root: Path,
        run_id: str,
        *,
        on_pause_wait: PauseObserver | None = None,
        on_resume: PauseObserver | None = None,
    ) -> RunSummary:
        """Continue a durable run, waiting at model boundaries while paused."""
        project_root = Path(project_root).resolve()
        store = ProjectSwarmStore(project_root)
        owner_token = str(uuid4())
        if not store.claim_run_execution_lease(run_id, owner_token):
            raise RuntimeError("Swarm execution is already active for this run")
        release_lease_here = True
        try:
            run = store.get_run(run_id)
            if run is None:
                raise KeyError(f"Unknown Swarm run: {run_id}")
            if run.status in {"completed", "cancelled", "abandoned"}:
                raise ValueError("Terminal Swarm runs cannot be executed again")
            options = self._resolve_execution_options(project_root, run)
            if options.blocked_reason is not None:
                return self._pause_before_execution_options(
                    store,
                    run_id,
                    options.blocked_reason,
                )
            engine, snapshot, _store = self._engine_for(project_root, options=options)
            summary = engine.execute_claimed_run(
                run_id,
                project_root,
                owner_token=owner_token,
                checkpoint=lambda: self._wait_for_running(
                    project_root,
                    run_id,
                    on_pause_wait=on_pause_wait,
                    on_resume=on_resume,
                ),
            )
            release_lease_here = False
            summary = self._record_catalog_unavailable(summary, snapshot, store)
            self._notify_run_completed(
                project_root,
                run_id,
                summary,
                options,
                store,
            )
            return summary
        finally:
            if release_lease_here:
                store.release_run_execution_lease(run_id, owner_token)

    def _engine_for(
        self,
        project_root: Path,
        *,
        options: SwarmExecutionOptions | None = None,
    ) -> tuple[SwarmEngine, ModelCatalogSnapshot | None, ProjectSwarmStore]:
        """Build from the only persisted healthy cloud catalog.

        No discovery happens here.  With no healthy explicit snapshot the core
        sees an empty catalog and records its normal durable pause rather than
        contacting a local model, another provider, or a fallback chain.
        """
        project_root = Path(project_root).resolve()
        store = ProjectSwarmStore(project_root)
        snapshot = store.get_model_catalog_snapshot(OLLAMA_CLOUD_PROVIDER)
        # The persisted snapshot proves the last explicit catalog refresh, not
        # the mutable process environment.  Recheck the endpoint before every
        # engine construction so a later OLLAMA_BASE_URL flip cannot route a
        # supposedly Cloud-only run to a local or third-party server.
        catalog = (
            snapshot.models
            if _is_verified_ollama_catalog(snapshot)
            and _uses_canonical_ollama_cloud_endpoint()
            else ()
        )
        transport = OllamaCloudTransport(
            self._call_llm,
            call_guard=lambda request: self._provider_slot(
                request.run_id, request.provider
            ),
        )
        return (
            SwarmEngine(
                transport,
                registry=ModelRegistry(catalog=catalog),
                max_calls=options.max_calls if options is not None else 48,
                max_concurrent=options.max_concurrent if options is not None else 3,
                verifier=options.verifier if options is not None else None,
                pre_completion_hook=(
                    options.pre_completion_hook if options is not None else None
                ),
                required_pre_completion_hook_id=(
                    options.required_pre_completion_hook_id if options is not None else None
                ),
            ),
            snapshot,
            store,
        )

    def _resolve_execution_options(
        self,
        project_root: Path,
        run: SwarmRun,
    ) -> SwarmExecutionOptions:
        """Resolve once from durable state, failing closed without error detail."""
        resolver = self._execution_options_resolver
        if resolver is None:
            if "required_pre_completion_hook" in run.metadata:
                return SwarmExecutionOptions(blocked_reason="invalid_execution_options")
            return SwarmExecutionOptions()
        try:
            resolved = resolver(project_root, run)
        except Exception:
            return SwarmExecutionOptions(blocked_reason="execution_options_unavailable")
        if resolved is None:
            if "required_pre_completion_hook" in run.metadata:
                return SwarmExecutionOptions(blocked_reason="invalid_execution_options")
            return SwarmExecutionOptions()
        if not isinstance(resolved, SwarmExecutionOptions):
            return SwarmExecutionOptions(blocked_reason="invalid_execution_options")
        if resolved.blocked_reason is not None:
            return replace(
                resolved,
                blocked_reason=_bounded_execution_options_reason(
                    resolved.blocked_reason
                ),
            )
        if not _valid_execution_options(run, resolved):
            return SwarmExecutionOptions(blocked_reason="invalid_execution_options")
        return resolved

    @staticmethod
    def _pause_before_execution_options(
        store: ProjectSwarmStore,
        run_id: str,
        reason: str,
    ) -> RunSummary:
        """Durably pause a bridge-blocked run before constructing an engine."""
        run = store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown Swarm run: {run_id}")
        if run.status in {"completed", "cancelled", "abandoned"}:
            raise ValueError("Terminal Swarm runs cannot be executed again")
        if run.status == "running":
            try:
                store.set_run_status(run_id, "paused")
            except (RuntimeError, ValueError):
                # A human pause may win the transition.  Either way this host
                # must not build an engine or make a model request.
                pass
        current = store.get_run(run_id)
        if current is None:
            raise KeyError(f"Unknown Swarm run: {run_id}")
        if current.status in {"completed", "cancelled", "abandoned"}:
            raise ValueError("Terminal Swarm runs cannot be executed again")
        if current.status != "paused":
            raise RuntimeError("Swarm run state changed during execution option resolution")
        store.append_event(
            run_id,
            "run.execution_blocked",
            {"reason": _bounded_execution_options_reason(reason)},
        )
        return RunSummary(
            run_id=run_id,
            status=current.status,
            call_count=0,
            evidence={},
            decision=None,
            pause_reason=_bounded_execution_options_reason(reason),
            events=tuple(store.list_events(run_id)),
        )

    @staticmethod
    def _record_catalog_unavailable(
        summary: RunSummary,
        snapshot: ModelCatalogSnapshot | None,
        store: ProjectSwarmStore,
    ) -> RunSummary:
        if summary.pause_reason == "no_eligible_model":
            store.append_event(
                summary.run_id,
                "model_catalog.unavailable",
                {
                    "provider": OLLAMA_CLOUD_PROVIDER,
                    "snapshot_present": snapshot is not None,
                    "healthy": bool(snapshot and snapshot.healthy),
                    "verified": _is_verified_ollama_catalog(snapshot),
                    "endpoint_trusted": _uses_canonical_ollama_cloud_endpoint(),
                    "source": snapshot.source
                    if snapshot is not None
                    else "not_refreshed",
                },
            )
            summary = replace(
                summary,
                events=tuple(store.list_events(summary.run_id)),
            )
        return summary

    @staticmethod
    def _notify_run_completed(
        project_root: Path,
        run_id: str,
        summary: RunSummary,
        options: SwarmExecutionOptions,
        store: ProjectSwarmStore,
    ) -> None:
        """Notify a process-only integration only after durable completion."""
        callback = options.on_completed
        if summary.status != "completed" or callback is None:
            return
        durable = store.get_run(run_id)
        if durable is None or durable.status != "completed":
            return
        try:
            callback(project_root, durable)
        except BaseException:
            # Completion is already durable. Unlike a Core execution hook,
            # this optional post-terminal observer cannot turn that success
            # into a caller-visible failure or leak its diagnostic details.
            try:
                store.append_event(
                    run_id,
                    "run.completion_observer_failed",
                    {"reason": "completion_observer_failed"},
                )
            except BaseException:
                pass

    def _wait_for_running(
        self,
        project_root: Path,
        run_id: str,
        *,
        on_pause_wait: PauseObserver | None = None,
        on_resume: PauseObserver | None = None,
    ) -> None:
        """Cooperatively honor a durable human pause without holding a model slot."""
        waiting = False
        while True:
            run = ProjectSwarmStore.open_read_only(project_root).get_run(run_id)
            if run is None:
                raise KeyError(f"Unknown Swarm run: {run_id}")
            if run.status == "running":
                if waiting and on_resume is not None:
                    on_resume()
                return
            if run.status == "paused":
                if not waiting and on_pause_wait is not None:
                    on_pause_wait()
                waiting = True
                time.sleep(self._pause_poll_seconds)
                continue
            raise RuntimeError("Cannot execute a terminal Swarm run")

    def list_runs(self, project_root: Path) -> list[SwarmRun]:
        return ProjectSwarmStore.open_read_only(project_root).list_runs()

    def get_run(self, project_root: Path, run_id: str) -> SwarmRun | None:
        return ProjectSwarmStore.open_read_only(project_root).get_run(run_id)

    def status(self, project_root: Path, run_id: str | None = None) -> dict[str, Any]:
        reader = ProjectSwarmStore.open_read_only(project_root)
        if run_id:
            run = reader.get_run(run_id)
            return {"run": run, "events": reader.list_events(run_id) if run else []}
        return {"runs": reader.list_runs()}

    def get_model_catalog(self, project_root: Path) -> ModelCatalogSnapshot | None:
        return ProjectSwarmStore.open_read_only(
            project_root
        ).get_model_catalog_snapshot(OLLAMA_CLOUD_PROVIDER)

    def list_packs(self, project_root: Path) -> tuple[object, ...]:
        """Read packaged/project metadata only; this never initializes Swarm."""
        return PackRegistry(project_root).list()

    def pause(self, project_root: Path, run_id: str) -> SwarmRun:
        ProjectSwarmStore.open_read_only(project_root)
        store = ProjectSwarmStore(project_root)
        run = store.set_run_status(run_id, "paused")
        store.append_event(run_id, "run.paused_by_human", {})
        return run

    def resume(self, project_root: Path, run_id: str) -> SwarmRun:
        ProjectSwarmStore.open_read_only(project_root)
        store = ProjectSwarmStore(project_root)
        run = store.resume_run(run_id)
        store.append_event(run_id, "run.resumed_by_human", {})
        return run

    def recover_execution_lease(
        self,
        project_root: Path,
        run_id: str,
        *,
        actor_id: str,
    ) -> SwarmRun:
        """Release an abandoned lease after an explicit human handoff.

        This never resumes or relaunches a workflow.  The operator must first
        confirm that the former host stopped, then separately use resume after
        inspecting the durable recovery audit event.
        """
        actor_id = _validated_host_recovery_actor(actor_id)
        project_root = Path(project_root).resolve()
        ProjectSwarmStore.open_read_only(project_root)
        return ProjectSwarmStore(project_root).recover_run_execution_lease(
            run_id,
            actor_id=actor_id,
        )

    def record_execution_failure(
        self,
        project_root: Path,
        run_id: str,
        *,
        error_type: str,
    ) -> SwarmRun:
        """Pause a failed synchronous continuation without persisting error text."""
        safe_error_type = _safe_execution_error_type(error_type)
        project_root = Path(project_root).resolve()
        store = ProjectSwarmStore(project_root)
        run = store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown Swarm run: {run_id}")
        if run.status in {"completed", "cancelled", "abandoned"}:
            return run
        if run.status == "running":
            try:
                store.set_run_status(run_id, "paused")
            except (RuntimeError, ValueError):
                # A human pause may have won the state transition between the
                # durable read and write.  It is already the desired result.
                pass
        current = store.get_run(run_id)
        if current is None:
            raise KeyError(f"Unknown Swarm run: {run_id}")
        if current.status not in {"completed", "cancelled", "abandoned"}:
            store.append_event(
                run_id,
                "run.execution_failed",
                {"error_type": safe_error_type},
            )
        return store.get_run(run_id) or current

    def record_human_approval(
        self,
        project_root: Path,
        run_id: str,
        proposal_id: str,
        *,
        actor_id: str,
        approved: bool = True,
    ) -> ApprovalRecord:
        """Record only an exact human approval/denial for a durable proposal.

        The event payload supplies the immutable requested action shape.  The
        host supplies its workspace and capability classification; callers
        cannot submit a model family, verifier evidence, or an approver id.
        This method intentionally has no action adapter or executor.
        """
        project_root = Path(project_root).resolve()
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("Human approval requires a host-derived actor")
        if not isinstance(approved, bool):
            raise TypeError("Human approval decision must be a bool")
        ProjectSwarmStore.open_read_only(project_root)
        store = ProjectSwarmStore(project_root)
        run = store.get_run(run_id)
        if run is None:
            raise ValueError("Unknown Swarm run")
        proposal = _proposal_from_event(
            store,
            run,
            proposal_id,
            self._action_classifier,
        )
        return store.record_approval(
            run.run_id,
            proposal.proposal_id,
            proposal_digest(proposal),
            "human",
            actor_id.strip(),
            approved=approved,
            model_family=None,
            evidence_refs=(),
        )


def _proposal_from_event(
    store: ProjectSwarmStore,
    run: SwarmRun,
    proposal_id: str,
    classifier: ActionClassifier,
) -> ActionProposal:
    if not isinstance(proposal_id, str) or not proposal_id.strip():
        raise ValueError("A proposal id is required")
    payload: Mapping[str, Any] | None = None
    for event in reversed(store.list_events(run.run_id)):
        if (
            event.event_type == "swarm.action_proposed"
            and event.payload.get("proposal_id") == proposal_id
        ):
            payload = event.payload
            break
    if payload is None:
        raise ValueError("No durable action proposal matches this approval")
    if set(payload) != {"proposal_id", "requested_action", "evidence_refs"}:
        raise ValueError("Durable action proposal has an unsafe shape")
    requested = payload.get("requested_action")
    evidence_refs = payload.get("evidence_refs")
    if not isinstance(requested, Mapping) or not isinstance(evidence_refs, list):
        raise ValueError("Durable action proposal has an unsafe shape")
    if set(requested) != {"name", "arguments", "use_worktree"}:
        raise ValueError("Durable action proposal has an unsafe action shape")
    if not isinstance(requested.get("arguments"), Mapping):
        raise ValueError("Durable action proposal arguments must be an object")
    if not isinstance(requested.get("use_worktree"), bool):
        raise ValueError("Durable action proposal worktree flag must be a bool")
    if any(
        not isinstance(reference, str) or not reference for reference in evidence_refs
    ):
        raise ValueError("Durable action proposal evidence must be strings")
    action = RequestedToolAction(
        name=str(requested.get("name") or ""),
        workspace=store.project_root,
        arguments=dict(requested["arguments"]),
        use_worktree=requested["use_worktree"],
    )
    capabilities = classifier(action)
    if not isinstance(capabilities, ActionCapabilities):
        raise TypeError("Swarm action classifier must return ActionCapabilities")
    return ActionProposal(
        proposal_id=proposal_id.strip(),
        category=capabilities.category,
        reversible=capabilities.reversible,
        external=capabilities.external,
        cost_increasing=capabilities.cost_increasing,
        evidence_refs=tuple(evidence_refs),
        requested_action=action,
    )


def _conservative_classifier(_action: RequestedToolAction) -> ActionCapabilities:
    return ActionCapabilities(
        category="unknown",
        reversible=False,
        external=True,
        cost_increasing=True,
    )


def _safe_execution_error_type(error_type: str) -> str:
    """Keep a durable failure marker useful without accepting raw error text."""
    if not isinstance(error_type, str):
        raise TypeError("Swarm execution error_type must be a string")
    cleaned = "".join(
        character
        for character in error_type.strip()
        if character.isascii() and (character.isalnum() or character in {"_", "-"})
    )
    return (cleaned or "Exception")[:128]


def _valid_execution_options(run: SwarmRun, options: SwarmExecutionOptions) -> bool:
    """Accept only durable-mode limits and well-shaped read-only extensions."""
    autonomy = run.metadata.get("autonomy")
    expected_max_calls = {
        "reviewed_execution": 48,
        "autonomous": 128,
    }.get(autonomy)
    return (
        expected_max_calls is not None
        and type(options.max_calls) is int
        and options.max_calls == expected_max_calls
        and type(options.max_concurrent) is int
        and 1 <= options.max_concurrent <= 3
        and _is_read_only_verifier(options.verifier)
        and _is_pre_completion_hook(options.pre_completion_hook)
        and _valid_required_pre_completion_hook_contract(run, options)
        and (options.on_completed is None or callable(options.on_completed))
    )


def _bounded_execution_options_reason(reason: object) -> str:
    """Map untrusted resolver data to fixed non-diagnostic durable tokens."""
    if type(reason) is str and reason in _ALLOWED_BLOCKED_EXECUTION_OPTION_REASONS:
        return reason
    return "execution_options_blocked"


def _is_read_only_verifier(verifier: object) -> bool:
    if verifier is None:
        return True
    try:
        return callable(getattr(verifier, "verify"))
    except Exception:
        return False


def _is_pre_completion_hook(hook: object) -> bool:
    if hook is None:
        return True
    try:
        hook_id = getattr(hook, "hook_id")
        return type(hook_id) is str and bool(hook_id.strip()) and callable(
            getattr(hook, "run")
        )
    except Exception:
        return False


def _valid_required_pre_completion_hook_contract(
    run: SwarmRun,
    options: SwarmExecutionOptions,
) -> bool:
    """Require resolver hook contracts to confirm the durable run requirement."""
    required_hook_id = options.required_pre_completion_hook_id
    durable_required_hook_id = run.metadata.get("required_pre_completion_hook")
    if durable_required_hook_id is not None:
        if type(durable_required_hook_id) is not str or not durable_required_hook_id.strip():
            return False
        if required_hook_id != durable_required_hook_id:
            return False
    if required_hook_id is None:
        return True
    if type(required_hook_id) is not str or not required_hook_id.strip():
        return False
    hook = options.pre_completion_hook
    if hook is None:
        return False
    try:
        return (
            getattr(hook, "hook_id") == required_hook_id
            and durable_required_hook_id == required_hook_id
        )
    except Exception:
        return False


def _validated_host_recovery_actor(actor_id: str) -> str:
    """Accept only a bounded principal from the CLI or trusted dashboard host."""
    if not isinstance(actor_id, str):
        raise TypeError("Swarm execution lease recovery actor must be a string")
    actor = actor_id.strip()
    if (
        not actor.startswith(("os:", "dashboard:"))
        or len(actor) > 256
        or any(ord(character) < 32 or ord(character) == 127 for character in actor)
    ):
        raise ValueError("Swarm execution lease recovery requires a host actor")
    prefix = "dashboard:" if actor.startswith("dashboard:") else "os:"
    if not actor[len(prefix) :].strip():
        raise ValueError("Swarm execution lease recovery requires a host actor")
    return actor


def _is_verified_ollama_catalog(snapshot: ModelCatalogSnapshot | None) -> bool:
    """Return whether a snapshot carries an exact live API routing proof."""
    return bool(
        snapshot is not None
        and snapshot.provider == OLLAMA_CLOUD_PROVIDER
        and snapshot.healthy
        and snapshot.source == OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE
    )


def _sidekick_call_llm(**kwargs: Any) -> Any:
    """Bind only Sidekick's existing auxiliary path, never a direct client."""
    # ``runtime.auxiliary_client`` resolves OLLAMA_BASE_URL for each request.
    # Check immediately before that hand-off too: an environment change after
    # engine construction must fail closed before any local endpoint receives
    # a Swarm prompt.
    if not _uses_canonical_ollama_cloud_endpoint():
        raise ModelProviderError("Swarm requires the canonical Ollama Cloud endpoint")
    from runtime.auxiliary_client import call_llm

    return call_llm(
        required_base_url=OLLAMA_CLOUD_CANONICAL_BASE_URL,
        **kwargs,
    )


def _refresh_ollama_catalog() -> ModelCatalogSnapshot:
    """Refresh Ollama Cloud only when an explicit write path asks for it."""
    from cli.models import fetch_api_models

    api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    base_url = _configured_ollama_cloud_base_url()
    live_models = (
        fetch_api_models(api_key, base_url, timeout=8.0)
        if api_key and _uses_canonical_ollama_cloud_endpoint(base_url)
        else None
    )
    # The generic Sidekick picker may merge models.dev and stale cache entries.
    # A Swarm snapshot is a routing proof, so it deliberately persists only the
    # IDs returned by this successful live Ollama Cloud API probe.
    models = tuple(live_models or ())
    return ModelCatalogSnapshot(
        provider=OLLAMA_CLOUD_PROVIDER,
        models=models,
        healthy=bool(models),
        source=(
            OLLAMA_CLOUD_VERIFIED_CATALOG_SOURCE
            if models
            else OLLAMA_CLOUD_UNAVAILABLE_CATALOG_SOURCE
        ),
    )


def _configured_ollama_cloud_base_url() -> str:
    """Return the unredacted endpoint only for local validation/dispatch."""
    return os.environ.get("OLLAMA_BASE_URL", "").strip() or OLLAMA_CLOUD_CANONICAL_BASE_URL


def _uses_canonical_ollama_cloud_endpoint(base_url: str | None = None) -> bool:
    """Accept only the public HTTPS Ollama Cloud API origin and v1 path.

    The generic Sidekick provider supports user overrides, including local
    Ollama.  Swarm's public contract is narrower: it must never treat such an
    override as an Ollama Cloud fallback.  Do not broaden this to subdomains,
    redirects, query strings, or arbitrary paths without a separate security
    review and explicit routing proof.
    """
    candidate = _configured_ollama_cloud_base_url() if base_url is None else base_url
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return bool(
        parsed.scheme.lower() == "https"
        and parsed.hostname == "ollama.com"
        and port in {None, 443}
        and parsed.path.rstrip("/") == "/v1"
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


@contextmanager
def _sidekick_ollama_provider_slot(run_id: str, provider: str) -> Iterator[None]:
    """Use a live Gateway pool when available, otherwise its configured limit.

    The gateway keeps its real ``AgentPool`` private to the active runner.
    When that runner is live, this guard acquires/releases that exact pool on
    its event loop.  CLI/web processes without a runner use a shared semaphore
    sized from the same provider configuration; Core's own three-call cap still
    applies in both cases.
    """
    if provider != OLLAMA_CLOUD_PROVIDER:
        raise ValueError("Swarm transport may only acquire ollama-cloud slots")
    live = _live_gateway_pool()
    if live is not None:
        pool, loop = live
        session_key = f"swarm:{run_id}"
        acquired = asyncio.run_coroutine_threadsafe(
            pool.acquire(session_key, provider), loop
        ).result(timeout=30)
        try:
            yield
        finally:
            if acquired:
                asyncio.run_coroutine_threadsafe(
                    pool.release(session_key, provider), loop
                ).result(timeout=30)
        return
    semaphore = _configured_ollama_semaphore()
    with semaphore:
        yield


def _live_gateway_pool() -> tuple[Any, Any] | None:
    try:
        from runtime.gateway import run as gateway_run

        runner_ref = getattr(gateway_run, "_gateway_runner_ref", None)
        runner = runner_ref() if callable(runner_ref) else None
        pool = getattr(runner, "_agent_pool", None)
        loop = getattr(runner, "_gateway_loop", None)
        if pool is not None and loop is not None and loop.is_running():
            return pool, loop
    except Exception:
        return None
    return None


def _configured_ollama_semaphore() -> threading.BoundedSemaphore:
    limit = 3
    try:
        from runtime.config import load_config
        from runtime.gateway.agent_pool import AgentPool

        limit = max(
            1,
            int(
                AgentPool(load_config()).get_pool(OLLAMA_CLOUD_PROVIDER).max_concurrent
            ),
        )
    except Exception:
        pass
    with _FALLBACK_SLOT_LOCK:
        semaphore = _FALLBACK_SLOTS.get(limit)
        if semaphore is None:
            semaphore = threading.BoundedSemaphore(limit)
            _FALLBACK_SLOTS[limit] = semaphore
        return semaphore
