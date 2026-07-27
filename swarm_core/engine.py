"""Project-local Swarm workflow engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .models import ModelRegistry
from .router import ModelRouter
from .store import ProjectSwarmStore
from .transport import ModelTransport
from .types import SwarmEvent
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
    ) -> RunSummary:
        if pack != "coding-team":
            raise ValueError(f"Unknown Swarm pack: {pack}")
        project_root = Path(project_root).resolve()
        store = ProjectSwarmStore(project_root)
        run = store.create_run(
            metadata={
                "goal": goal,
                "pack": pack,
                "project_root": str(project_root),
            }
        )
        store.append_event(
            run.run_id,
            "run.started",
            {"goal": goal, "pack": pack},
        )
        executor = ModelExecutor(
            ModelRouter(self.registry),
            self.transport,
            call_budget=CallBudget(self.max_calls),
            max_concurrent=self.max_concurrent,
        )

        def emit(event_type: str, payload: Mapping[str, object]) -> None:
            store.append_event(run.run_id, event_type, payload)

        try:
            outcome = CodingTeamWorkflow().run(
                run_id=run.run_id,
                goal=goal,
                project_root=project_root,
                executor=executor,
                emit=emit,
            )
        except WorkflowPaused as paused:
            store.set_run_status(run.run_id, "paused")
            store.append_event(
                run.run_id,
                "run.paused",
                {
                    "attempted_models": list(paused.attempted_models),
                    "reason": paused.reason,
                    "role": paused.role,
                },
            )
            events = tuple(store.list_events(run.run_id))
            partial_evidence = {
                str(event.payload["role"]): list(event.payload["evidence"])
                for event in events
                if event.event_type == "evidence.recorded"
                and isinstance(event.payload.get("evidence"), list)
            }
            return RunSummary(
                run_id=run.run_id,
                status="paused",
                call_count=executor.call_budget.used,
                evidence=partial_evidence,
                decision=None,
                pause_reason=paused.reason,
                events=events,
            )

        store.set_run_status(run.run_id, "completed")
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
