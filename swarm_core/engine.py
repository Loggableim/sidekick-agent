"""Project-local Swarm workflow engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from .models import ModelRegistry
from .packs import PackRegistry
from .router import ModelRouter
from .store import ProjectSwarmStore
from .transport import ModelTransport
from .types import SwarmEvent, SwarmRun
from .workflow import (
    CallBudget,
    CodingTeamWorkflow,
    ModelExecutor,
    WorkflowPaused,
)


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    status: str
    call_count: int
    evidence: Mapping[str, list[object]]
    decision: str | None
    pause_reason: str | None
    events: tuple[SwarmEvent, ...]


class SwarmEngine:
    def __init__(
        self,
        transport: ModelTransport,
        *,
        registry: ModelRegistry | None = None,
        max_calls: int = 48,
        max_concurrent: int = 3,
    ) -> None:
        self.transport = transport
        self.registry = registry or ModelRegistry()
        self.max_calls = max_calls
        self.max_concurrent = max_concurrent

    def run(
        self,
        goal: str,
        project_root: Path,
        pack: str = "coding-team",
        *,
        autonomy: str | None = None,
    ) -> RunSummary:
        """Create and synchronously execute a run for existing CLI callers."""
        run = self.start_run(goal, project_root, pack=pack, autonomy=autonomy)
        return self.execute_run(run.run_id, project_root)

    def start_run(
        self,
        goal: str,
        project_root: Path,
        pack: str = "coding-team",
        *,
        autonomy: str | None = None,
    ) -> SwarmRun:
        """Persist a runnable run before any model invocation begins.

        Hosts that expose an asynchronous UI can return this durable identity
        immediately, then call :meth:`execute_run` from their tracked worker.
        ``run`` above remains the synchronous convenience API.
        """
        project_root = Path(project_root).resolve()
        PackRegistry(project_root).get(pack)
        store = ProjectSwarmStore(project_root)
        metadata = {
            "goal": goal,
            "pack": pack,
            "project_root": str(project_root),
        }
        if autonomy is not None:
            metadata["autonomy"] = autonomy
        run = store.create_run(metadata=metadata)
        store.append_event(
            run.run_id,
            "run.started",
            {"goal": goal, "pack": pack},
        )
        return run

    def execute_run(
        self,
        run_id: str,
        project_root: Path,
        *,
        checkpoint: Callable[[], None] | None = None,
    ) -> RunSummary:
        """Execute one already persisted run without recreating its identity.

        ``checkpoint`` is host-neutral cooperative control.  A host may wait
        while the durable run is paused; Core calls it before every model
        attempt and before the terminal completion transition.
        """
        project_root = Path(project_root).resolve()
        store = ProjectSwarmStore(project_root)
        run = store.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown Swarm run: {run_id}")
        if run.status == "completed":
            raise ValueError("Completed Swarm runs cannot be executed again")
        if run.status == "paused" and checkpoint is None:
            raise ValueError("Paused Swarm runs require a checkpoint-aware host")

        goal, pack = self._durable_run_inputs(run, project_root)
        pack_definition = PackRegistry(project_root).get(pack)
        if checkpoint is not None:
            checkpoint()
        executor = ModelExecutor(
            ModelRouter(self.registry),
            self.transport,
            call_budget=CallBudget(self.max_calls),
            max_concurrent=self.max_concurrent,
            before_model_call=checkpoint,
        )

        def emit(event_type: str, payload: Mapping[str, object]) -> None:
            store.append_event(run.run_id, event_type, payload)

        try:
            outcome = CodingTeamWorkflow(
                pack_id=pack_definition.pack_id,
                pack_description=pack_definition.description,
                pack_roles=pack_definition.roles,
            ).run(
                run_id=run.run_id,
                goal=goal,
                project_root=project_root,
                executor=executor,
                emit=emit,
            )
        except WorkflowPaused as paused:
            return self._pause_summary(store, run.run_id, executor, paused)

        if not self._complete_after_checkpoint(store, run.run_id, checkpoint):
            events = tuple(store.list_events(run.run_id))
            return RunSummary(
                run_id=run.run_id,
                status="paused",
                call_count=executor.call_budget.used,
                evidence=outcome.evidence,
                decision=None,
                pause_reason="human_paused",
                events=events,
            )
        store.append_event(
            run.run_id,
            "run.completed",
            {
                "call_count": executor.call_budget.used,
                "decision": outcome.decision,
            },
        )
        return RunSummary(
            run_id=run.run_id,
            status="completed",
            call_count=executor.call_budget.used,
            evidence=outcome.evidence,
            decision=outcome.decision,
            pause_reason=None,
            events=tuple(store.list_events(run.run_id)),
        )

    @staticmethod
    def _durable_run_inputs(run: SwarmRun, project_root: Path) -> tuple[str, str]:
        """Read immutable execution inputs from the run returned to a host."""
        metadata = run.metadata
        goal = metadata.get("goal")
        pack = metadata.get("pack")
        stored_project_root = metadata.get("project_root")
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Swarm run is missing a durable goal")
        if not isinstance(pack, str) or not pack.strip():
            raise ValueError("Swarm run is missing a durable pack")
        if stored_project_root != str(project_root):
            raise ValueError("Swarm run belongs to a different project")
        return goal, pack

    @staticmethod
    def _pause_summary(
        store: ProjectSwarmStore,
        run_id: str,
        executor: ModelExecutor,
        paused: WorkflowPaused,
    ) -> RunSummary:
        """Durably pause once while preserving an already human-paused run."""
        current = store.get_run(run_id)
        if current is None:
            raise KeyError(f"Unknown Swarm run: {run_id}")
        if current.status == "running":
            try:
                store.set_run_status(run_id, "paused")
            except (RuntimeError, ValueError):
                # A human pause can win after the read above but before the
                # SQLite transition.  It is the same durable paused outcome,
                # not an execution error for a background host to report.
                current = store.get_run(run_id)
                if current is None:
                    raise KeyError(f"Unknown Swarm run: {run_id}")
                if current.status != "paused":
                    raise
        elif current.status != "paused":
            raise RuntimeError("Swarm run reached a terminal state during execution")
        store.append_event(
            run_id,
            "run.paused",
            {
                "attempted_models": list(paused.attempted_models),
                "reason": paused.reason,
                "role": paused.role,
            },
        )
        events = tuple(store.list_events(run_id))
        partial_evidence = {
            str(event.payload["role"]): list(event.payload["evidence"])
            for event in events
            if event.event_type == "evidence.recorded"
            and isinstance(event.payload.get("evidence"), list)
        }
        return RunSummary(
            run_id=run_id,
            status="paused",
            call_count=executor.call_budget.used,
            evidence=partial_evidence,
            decision=None,
            pause_reason=paused.reason,
            events=events,
        )

    @staticmethod
    def _complete_after_checkpoint(
        store: ProjectSwarmStore,
        run_id: str,
        checkpoint: Callable[[], None] | None,
    ) -> bool:
        """Avoid a human pause racing the terminal completed transition.

        ``False`` means a host without a cooperative checkpoint owns the
        durable paused state, so callers must not append a completion event.
        """
        while True:
            if checkpoint is not None:
                checkpoint()
            try:
                store.set_run_status(run_id, "completed")
                return True
            except ValueError:
                current = store.get_run(run_id)
                if (
                    current is not None
                    and current.status == "paused"
                    and checkpoint is not None
                ):
                    # A pause can land after the checkpoint but before SQLite's
                    # transition lock.  Wait for the explicit human resume.
                    continue
                if current is not None and current.status == "paused":
                    return False
                raise
