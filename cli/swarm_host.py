"""Sidekick-owned bindings for project-local Swarm Core.

This module is deliberately outside ``swarm_core``: it is the only place that
knows how to call Sidekick's auxiliary LLM path, resolve provider capacity, and
derive a host-side human identity.  It never executes a proposed tool action.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable, ContextManager, Iterator, Mapping

from swarm_core.engine import RunSummary, SwarmEngine
from swarm_core.models import (
    ModelCatalogSnapshot,
    ModelRegistry,
    OLLAMA_CLOUD_PROVIDER,
)
from swarm_core.packs import PackRegistry
from swarm_core.policy import proposal_digest
from swarm_core.store import ProjectSwarmStore
from swarm_core.transport import OllamaCloudTransport
from swarm_core.types import (
    ActionCapabilities,
    ActionProposal,
    ApprovalRecord,
    RequestedToolAction,
    SwarmRun,
)


CatalogRefresher = Callable[[], ModelCatalogSnapshot]
ProviderSlot = Callable[[str, str], ContextManager[None]]
ActionClassifier = Callable[[RequestedToolAction], ActionCapabilities]
PauseObserver = Callable[[], None]

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
        pause_poll_seconds: float = 0.05,
    ) -> None:
        # Constructor purity matters: GET route handlers may construct a
        # service for dependency injection, but must never create `.swarm`.
        self._call_llm = call_llm or _sidekick_call_llm
        self._catalog_refresher = catalog_refresher or _refresh_ollama_catalog
        self._provider_slot = provider_slot or _sidekick_ollama_provider_slot
        self._action_classifier = action_classifier or _conservative_classifier
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
    ) -> RunSummary:
        """Synchronously run the same durable lifecycle used by the CLI."""
        run = self.start_run(goal, project_root, pack=pack)
        return self.execute_run(project_root, run.run_id)

    def start_run(
        self,
        goal: str,
        project_root: Path,
        *,
        pack: str = "coding-team",
    ) -> SwarmRun:
        """Persist a running Swarm identity without starting a model call."""
        project_root = Path(project_root).resolve()
        engine, _snapshot, _store = self._engine_for(project_root)
        return engine.start_run(goal, project_root, pack=pack)

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
        engine, snapshot, store = self._engine_for(project_root)
        summary = engine.execute_run(
            run_id,
            project_root,
            checkpoint=lambda: self._wait_for_running(
                project_root,
                run_id,
                on_pause_wait=on_pause_wait,
                on_resume=on_resume,
            ),
        )
        return self._record_catalog_unavailable(summary, snapshot, store)

    def _engine_for(
        self,
        project_root: Path,
    ) -> tuple[SwarmEngine, ModelCatalogSnapshot | None, ProjectSwarmStore]:
        """Build from the only persisted healthy cloud catalog.

        No discovery happens here.  With no healthy explicit snapshot the core
        sees an empty catalog and records its normal durable pause rather than
        contacting a local model, another provider, or a fallback chain.
        """
        project_root = Path(project_root).resolve()
        store = ProjectSwarmStore(project_root)
        snapshot = store.get_model_catalog_snapshot(OLLAMA_CLOUD_PROVIDER)
        catalog = (
            snapshot.models
            if snapshot is not None
            and snapshot.healthy
            and snapshot.provider == OLLAMA_CLOUD_PROVIDER
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
            ),
            snapshot,
            store,
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
            raise RuntimeError("Cannot execute a completed Swarm run")

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


def _sidekick_call_llm(**kwargs: Any) -> Any:
    """Bind only Sidekick's existing auxiliary path, never a direct client."""
    from runtime.auxiliary_client import call_llm

    return call_llm(**kwargs)


def _refresh_ollama_catalog() -> ModelCatalogSnapshot:
    """Refresh Ollama Cloud only when an explicit write path asks for it."""
    from cli.models import fetch_api_models, fetch_ollama_cloud_models

    api_key = os.environ.get("OLLAMA_API_KEY", "").strip()
    base_url = os.environ.get("OLLAMA_BASE_URL", "").strip() or "https://ollama.com/v1"
    live_models = fetch_api_models(api_key, base_url, timeout=8.0) if api_key else None
    # The Sidekick helper can expose a stale/models.dev list.  It is useful for
    # the explicit UI catalog, but only a successful live probe marks it usable
    # for a Swarm run.
    models = fetch_ollama_cloud_models(
        api_key=api_key or None,
        base_url=base_url,
        force_refresh=True,
    )
    return ModelCatalogSnapshot(
        provider=OLLAMA_CLOUD_PROVIDER,
        models=tuple(models),
        healthy=bool(live_models),
        source="ollama-cloud-live" if live_models else "ollama-cloud-live-unavailable",
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
