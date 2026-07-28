"""Project-local Swarm workflow engine."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from uuid import uuid4

from .learning import ReputationLedger
from .models import ModelRegistry, ModelResponse
from .packs import PackRegistry
from .router import ModelRouter
from .store import ProjectSwarmStore
from .transport import ModelTransport
from .types import SwarmEvent, SwarmRun, WorkflowRoleCheckpoint
from .verifier import (
    InvalidVerifierResult,
    ReadOnlyVerifier,
    VerificationRequest,
    validate_independent_result,
    verification_result_from_checkpoint_data,
)
from .workflow import (
    CallBudget,
    CodingTeamWorkflow,
    ModelExecutor,
    ModelAttemptStarted,
    WorkflowPaused,
)


_ROLE_PREREQUISITES: Mapping[str, frozenset[str]] = {
    "scout": frozenset(),
    "planner": frozenset({"scout"}),
    "planner_challenger": frozenset({"scout", "planner"}),
    "planner_arbitrator": frozenset({"planner", "planner_challenger"}),
    "builder": frozenset({"planner"}),
    "critic": frozenset({"planner"}),
    "verifier": frozenset({"builder", "critic"}),
    "review_a": frozenset({"verifier"}),
    "review_b": frozenset({"verifier"}),
    "integrator": frozenset({"review_a", "review_b"}),
    "referee": frozenset({"integrator"}),
}
_WORKFLOW_COMPLETION_EVENT_TYPES = frozenset(
    {"work.completed", "evidence.recorded", "decision.recorded"}
)
# Generic completion event names are intentionally reserved for the Core
# workflow.  A separate integration that needs to record similarly shaped
# data must opt into one of these explicit non-workflow role namespaces; an
# unqualified unknown role could otherwise be a legacy workflow stage that we
# must not silently replay from Scout.
_NON_WORKFLOW_ROLE_PREFIXES = ("external/", "integration/", "plugin/", "nova/")
_HOST_METADATA_RESERVED_KEYS = frozenset({"goal", "pack", "project_root", "autonomy"})


def _is_non_workflow_role(role: object) -> bool:
    """Whether an explicitly namespaced integration role is safe to ignore."""
    return isinstance(role, str) and any(
        len(role) > len(prefix) and role.startswith(prefix)
        for prefix in _NON_WORKFLOW_ROLE_PREFIXES
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


@dataclass(frozen=True)
class PreCompletionContext:
    run: SwarmRun
    project_root: Path
    store: ProjectSwarmStore
    goal: str
    pack: str
    autonomy: str
    call_count: int
    decision: str
    evidence: Mapping[str, list[Any]]


@dataclass(frozen=True)
class PreCompletionResult:
    continue_completion: bool
    pause_reason: str | None = None

    def __post_init__(self) -> None:
        if type(self.continue_completion) is not bool:
            raise ValueError("Pre-completion continuation must be a boolean")
        if not self.continue_completion and not (
            isinstance(self.pause_reason, str) and self.pause_reason.strip()
        ):
            raise ValueError("Paused pre-completion result requires a reason")
        if self.continue_completion and self.pause_reason is not None:
            raise ValueError("Continuing result cannot carry a pause reason")


class PreCompletionHook(Protocol):
    hook_id: str

    def run(self, context: PreCompletionContext) -> PreCompletionResult: ...


@dataclass(frozen=True)
class _UnmatchedModelAttempt:
    sequence: int
    role: str
    model: str


@dataclass(frozen=True)
class _ModelAttemptRecovery:
    failed_models: Mapping[str, frozenset[str]]
    unmatched_attempts: tuple[_UnmatchedModelAttempt, ...]
    invalid_replay_authorization: bool


class SwarmEngine:
    def __init__(
        self,
        transport: ModelTransport,
        *,
        registry: ModelRegistry | None = None,
        max_calls: int = 48,
        max_concurrent: int = 3,
        verifier: ReadOnlyVerifier | None = None,
        pre_completion_hook: PreCompletionHook | None = None,
    ) -> None:
        self.transport = transport
        self.registry = registry or ModelRegistry()
        self.max_calls = max_calls
        self.max_concurrent = max_concurrent
        self.verifier = verifier
        self.pre_completion_hook = pre_completion_hook

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
        host_metadata: Mapping[str, Any] | None = None,
    ) -> SwarmRun:
        """Persist a runnable run before any model invocation begins.

        Hosts that expose an asynchronous UI can return this durable identity
        immediately, then call :meth:`execute_run` from their tracked worker.
        ``run`` above remains the synchronous convenience API.
        """
        project_root = Path(project_root).resolve()
        PackRegistry(project_root).get(pack)
        store = ProjectSwarmStore(project_root)
        metadata = self._validated_host_metadata(host_metadata)
        metadata.update({
            "goal": goal,
            "pack": pack,
            "project_root": str(project_root),
        })
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
        owner_token = str(uuid4())
        if not store.claim_run_execution_lease(run_id, owner_token):
            raise RuntimeError("Swarm execution is already active for this run")
        return self.execute_claimed_run(
            run_id,
            project_root,
            owner_token=owner_token,
            checkpoint=checkpoint,
        )

    def execute_claimed_run(
        self,
        run_id: str,
        project_root: Path,
        *,
        owner_token: str,
        checkpoint: Callable[[], None] | None = None,
    ) -> RunSummary:
        """Execute and release an already-claimed durable host lease.

        A host with a pre-dispatch admission step can safely resolve its
        options only after it owns the same lease that protects Core model
        execution.  The owner token remains private to that host invocation.
        """
        project_root = Path(project_root).resolve()
        store = ProjectSwarmStore(project_root)
        try:
            if not store.run_execution_lease_is_owned(run_id, owner_token):
                raise RuntimeError("Swarm execution lease is not owned by this host")
            # Refresh only after the atomic claim.  Otherwise an executor
            # that observed ``running`` before another owner completed could
            # claim a newly released lease and replay a terminal run from a
            # stale in-memory Run object.
            run = store.get_run(run_id)
            if run is None:  # Defensive: the claim already checked this.
                raise KeyError(f"Unknown Swarm run: {run_id}")
            if run.status == "completed":
                raise ValueError("Completed Swarm runs cannot be executed again")
            if run.status == "paused" and checkpoint is None:
                raise ValueError("Paused Swarm runs require a checkpoint-aware host")
            # The lease deliberately covers human pause waits as well as
            # provider calls.  A second host must not take the same run while
            # an in-process host is cooperatively waiting for a resume.
            return self._execute_claimed_run(
                store,
                run,
                project_root,
                checkpoint=checkpoint,
            )
        finally:
            store.release_run_execution_lease(run_id, owner_token)

    def _execute_claimed_run(
        self,
        store: ProjectSwarmStore,
        run: SwarmRun,
        project_root: Path,
        *,
        checkpoint: Callable[[], None] | None,
    ) -> RunSummary:
        """Execute a run after :meth:`execute_run` has claimed its lease."""
        goal, pack = self._durable_run_inputs(run, project_root)
        pack_definition = PackRegistry(project_root).get(pack)
        if checkpoint is not None:
            checkpoint()
        prior_events = store.list_events(run.run_id)
        completed_responses = self._restore_completed_responses(
            store,
            run.run_id,
            prior_events,
        )
        attempt_recovery = self._recover_model_attempts(prior_events)
        prior_call_count = self._prior_model_call_count(prior_events)
        executor = ModelExecutor(
            ModelRouter(self.registry),
            self.transport,
            call_budget=CallBudget(
                self.max_calls,
                initial_used=prior_call_count,
            ),
            max_concurrent=self.max_concurrent,
            before_model_call=checkpoint,
            on_model_attempt_started=lambda attempt: self._record_model_attempt_started(
                store,
                run.run_id,
                attempt,
            ),
            prior_failed_models=attempt_recovery.failed_models,
        )

        if attempt_recovery.invalid_replay_authorization:
            return self._pause_summary(
                store,
                run.run_id,
                executor,
                WorkflowPaused(
                    "invalid_model_attempt_replay_authorization",
                    role="recovery",
                ),
            )
        if (
            attempt_recovery.unmatched_attempts
            and not self._transport_supports_idempotent_replay()
        ):
            unmatched = attempt_recovery.unmatched_attempts[0]
            return self._pause_summary(
                store,
                run.run_id,
                executor,
                WorkflowPaused(
                    "unmatched_model_attempt",
                    role=unmatched.role,
                    attempted_models=(unmatched.model,),
                ),
            )

        def emit(event_type: str, payload: Mapping[str, object]) -> None:
            store.append_event(run.run_id, event_type, payload)

        def record_response(role: str, response: ModelResponse) -> ModelResponse:
            checkpoint_record, _created = store.record_workflow_role_checkpoint(
                run.run_id,
                role,
                model=response.model or None,
                data=response.data,
            )
            return self._response_from_checkpoint(checkpoint_record)

        try:
            outcome = CodingTeamWorkflow(
                pack_id=pack_definition.pack_id,
                workflow=pack_definition.workflow,
                pack_description=pack_definition.description,
                pack_roles=pack_definition.roles,
                verifier=self.verifier,
            ).run(
                run_id=run.run_id,
                goal=goal,
                project_root=project_root,
                executor=executor,
                emit=emit,
                completed_responses=completed_responses,
                record_response=record_response,
            )
        except WorkflowPaused as paused:
            return self._pause_summary(store, run.run_id, executor, paused)

        try:
            self._record_local_verifier_reputation(
                store,
                run_id=run.run_id,
                goal=goal,
                project_root=project_root,
            )
        except (InvalidVerifierResult, TypeError, ValueError):
            return self._pause_summary(
                store,
                run.run_id,
                executor,
                WorkflowPaused("invalid_verifier_result", role="verifier"),
            )

        pre_completion_pause = self._run_pre_completion_hook(
            store,
            run,
            project_root=project_root,
            goal=goal,
            pack=pack,
            call_count=executor.call_budget.used,
            decision=outcome.decision,
            evidence=outcome.evidence,
            checkpoint=checkpoint,
        )
        if pre_completion_pause is not None:
            return self._pause_summary(
                store,
                run.run_id,
                executor,
                pre_completion_pause,
            )

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
    def _validated_host_metadata(
        host_metadata: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Accept durable host metadata without allowing Core input overrides."""
        if host_metadata is None:
            return {}
        if not isinstance(host_metadata, Mapping):
            raise ValueError("Swarm host metadata must be a mapping")
        metadata = dict(host_metadata)
        reserved = _HOST_METADATA_RESERVED_KEYS & set(metadata)
        if reserved:
            raise ValueError(
                "Swarm host metadata cannot override "
                + ", ".join(sorted(reserved))
            )
        try:
            serialized = json.dumps(metadata, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Swarm host metadata must be JSON-safe") from exc
        if json.loads(serialized) != metadata:
            raise ValueError("Swarm host metadata must be JSON-safe")
        return metadata

    def _run_pre_completion_hook(
        self,
        store: ProjectSwarmStore,
        run: SwarmRun,
        *,
        project_root: Path,
        goal: str,
        pack: str,
        call_count: int,
        decision: str,
        evidence: Mapping[str, list[Any]],
        checkpoint: Callable[[], None] | None,
    ) -> WorkflowPaused | None:
        """Run a host-neutral durable completion gate, when the run requires it."""
        required_hook_key = "required_pre_completion_hook"
        if required_hook_key not in run.metadata:
            return None
        required_hook_id = run.metadata[required_hook_key]
        if type(required_hook_id) is not str or not required_hook_id.strip():
            return WorkflowPaused(
                "required_pre_completion_hook_unavailable",
                role="pre_completion_hook",
            )
        hook = self.pre_completion_hook
        if hook is None:
            return WorkflowPaused(
                "required_pre_completion_hook_unavailable",
                role="pre_completion_hook",
            )
        try:
            installed_hook_id = hook.hook_id
        except Exception:
            return WorkflowPaused("pre_completion_hook_failed", role="pre_completion_hook")
        if type(installed_hook_id) is not str or not installed_hook_id.strip():
            return WorkflowPaused("pre_completion_hook_failed", role="pre_completion_hook")
        if installed_hook_id != required_hook_id:
            return WorkflowPaused(
                "required_pre_completion_hook_unavailable",
                role="pre_completion_hook",
            )

        while True:
            if checkpoint is not None:
                checkpoint()
            active_run = store.get_run(run.run_id)
            if active_run is None:
                raise KeyError(f"Unknown Swarm run: {run.run_id}")
            if active_run.status == "running":
                break
            if active_run.status == "paused" and checkpoint is not None:
                # A cooperative host may resume while waiting.  Re-enter the
                # required hook gate rather than letting the terminal
                # transition complete after a pause/resume race.
                continue
            if active_run.status == "paused":
                return WorkflowPaused("human_paused", role="pre_completion_hook")
            raise RuntimeError("Swarm run reached a terminal state before completion")
        autonomy = active_run.metadata.get("autonomy")
        if not isinstance(autonomy, str):
            raise ValueError("Swarm run is missing a durable autonomy setting")
        context = PreCompletionContext(
            run=active_run,
            project_root=project_root,
            store=store,
            goal=goal,
            pack=pack,
            autonomy=autonomy,
            call_count=call_count,
            decision=decision,
            evidence=evidence,
        )
        try:
            result = hook.run(context)
            if not isinstance(result, PreCompletionResult):
                raise TypeError("Pre-completion hook returned an invalid result")
            if result.continue_completion:
                return None
            return WorkflowPaused(result.pause_reason, role="pre_completion_hook")
        except Exception:
            return WorkflowPaused("pre_completion_hook_failed", role="pre_completion_hook")

    @staticmethod
    def _record_local_verifier_reputation(
        store: ProjectSwarmStore,
        *,
        run_id: str,
        goal: str,
        project_root: Path,
    ) -> None:
        """Consume only a validated, durable local-verifier assessment set.

        The workflow has already persisted the verifier's read-only result as
        one checkpoint.  Reconstructing and independently re-validating that
        shape here prevents model evidence or a corrupted checkpoint from
        reaching learning.  Store-level source deduplication makes a crash or
        resume at this run-closing boundary idempotent.
        """
        checkpoints = store.get_workflow_role_checkpoints(run_id)
        verifier_checkpoint = checkpoints.get("verifier")
        builder_checkpoint = checkpoints.get("builder")
        critic_checkpoint = checkpoints.get("critic")
        if (
            verifier_checkpoint is None
            or builder_checkpoint is None
            or critic_checkpoint is None
        ):
            raise InvalidVerifierResult(
                "Reputation requires durable builder, critic, and verifier checkpoints"
            )
        request = VerificationRequest(
            run_id=run_id,
            goal=goal,
            project_root=project_root,
            builder=builder_checkpoint.data,
            critic=critic_checkpoint.data,
        )
        result = validate_independent_result(
            verification_result_from_checkpoint_data(verifier_checkpoint.data),
            request,
        )
        ledger = ReputationLedger(store)
        for assessment in result.assessments:
            ledger.record_local_verifier_assessment(assessment)

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
    def _restore_completed_responses(
        store: ProjectSwarmStore,
        run_id: str,
        events: list[SwarmEvent],
    ) -> dict[str, ModelResponse]:
        """Rebuild a bounded Blackboard prefix from durable role checkpoints.

        New role outputs are committed atomically with their three evidence
        events.  The conservative event replay branch preserves resumability
        for a run created by the immediately preceding implementation: only a
        complete, unambiguous work/evidence/decision triple may be reused.
        Ambiguous partial history fails closed rather than replaying an action
        prompt from Scout and silently duplicating model work.
        """
        checkpoints = store.get_workflow_role_checkpoints(run_id)
        restored = {
            role: SwarmEngine._response_from_checkpoint(checkpoint)
            for role, checkpoint in checkpoints.items()
        }
        if not set(restored).issubset(_ROLE_PREREQUISITES):
            raise RuntimeError("Swarm run has an unknown durable workflow role")

        by_role: dict[str, dict[str, list[SwarmEvent]]] = {}
        for event in events:
            if event.event_type not in _WORKFLOW_COMPLETION_EVENT_TYPES:
                continue
            role = event.payload.get("role")
            if _is_non_workflow_role(role):
                continue
            if not isinstance(role, str) or role not in _ROLE_PREREQUISITES:
                raise RuntimeError("Swarm run has an unknown durable workflow role")
            if role in restored:
                continue
            by_role.setdefault(role, {}).setdefault(event.event_type, []).append(event)

        for role, grouped in by_role.items():
            work = grouped.get("work.completed", [])
            evidence = grouped.get("evidence.recorded", [])
            decision = grouped.get("decision.recorded", [])
            if len(work) != 1 or len(evidence) != 1 or len(decision) != 1:
                raise RuntimeError(
                    "Swarm run has an incomplete or ambiguous legacy workflow "
                    f"record for role: {role}"
                )
            if not (work[0].sequence < evidence[0].sequence < decision[0].sequence):
                raise RuntimeError(
                    "Swarm run has an out-of-order legacy workflow record "
                    f"for role: {role}"
                )
            work_value = work[0].payload.get("work")
            evidence_value = evidence[0].payload.get("evidence")
            decision_value = decision[0].payload.get("decision")
            if (
                not isinstance(work_value, str)
                or not isinstance(evidence_value, list)
                or not isinstance(decision_value, str)
            ):
                raise RuntimeError(
                    f"Swarm run has an invalid legacy workflow record for role: {role}"
                )
            model = work[0].payload.get("model")
            if role == "verifier":
                valid_model = model is None or (
                    isinstance(model, str) and bool(model.strip())
                )
            else:
                valid_model = isinstance(model, str) and bool(model.strip())
            if not valid_model:
                raise RuntimeError(
                    f"Swarm run has an invalid legacy model record for role: {role}"
                )
            restored[role] = ModelResponse(
                model=model.strip() if isinstance(model, str) else "",
                content="",
                data={
                    "work": work_value,
                    "evidence": list(evidence_value),
                    "decision": decision_value,
                },
            )

        for role, prerequisites in _ROLE_PREREQUISITES.items():
            if role in restored and not prerequisites.issubset(restored):
                raise RuntimeError(
                    "Swarm run has an out-of-order durable workflow checkpoint "
                    f"for role: {role}"
                )
        return restored

    @staticmethod
    def _recover_model_attempts(events: list[SwarmEvent]) -> _ModelAttemptRecovery:
        """Recover only provider attempts that were durably resolved.

        A failure record proves that exact role/model attempt should not be
        charged or dispatched again.  A durable dispatch marker without a
        later failure or successful work record is ambiguous: the provider may
        have received it just before the process died.  Callers pause on that
        ambiguity unless their transport explicitly guarantees idempotent
        replay.
        """
        pending: list[_UnmatchedModelAttempt] = []
        failed_models: dict[str, set[str]] = {}
        authorized_sequences: set[int] = set()
        invalid_replay_authorization = False
        for event in sorted(events, key=lambda item: item.sequence):
            if event.event_type == "model.attempt_started":
                identity = SwarmEngine._model_attempt_identity(event)
                if identity is not None:
                    pending.append(_UnmatchedModelAttempt(event.sequence, *identity))
                continue
            if event.event_type == "model.attempt_failed":
                identity = SwarmEngine._model_attempt_identity(event)
                if identity is not None:
                    role, model = identity
                    failed_models.setdefault(role, set()).add(model)
                    SwarmEngine._consume_pending_attempt(pending, role, model)
                continue
            if event.event_type == "work.completed":
                identity = SwarmEngine._workflow_completion_identity(event)
                if identity is not None:
                    SwarmEngine._consume_pending_attempt(pending, *identity)
                continue
            if event.event_type != "model.attempt_replay_authorized_by_human":
                continue
            identity = SwarmEngine._replay_authorization_identity(event)
            if identity is None:
                invalid_replay_authorization = True
                continue
            original_sequence, role, model = identity
            matching_attempts = [
                attempt
                for attempt in pending
                if (
                    attempt.sequence == original_sequence
                    and attempt.role == role
                    and attempt.model == model
                )
            ]
            if (
                event.sequence <= original_sequence
                or original_sequence in authorized_sequences
                or len(matching_attempts) != 1
            ):
                invalid_replay_authorization = True
                continue
            authorized_sequences.add(original_sequence)
            pending = [
                attempt for attempt in pending if attempt.sequence != original_sequence
            ]
            # An explicit human handoff permits this exact fresh retry even
            # if an earlier attempt with the same role/model failed.
            failed_models.get(role, set()).discard(model)
        return _ModelAttemptRecovery(
            failed_models={
                role: frozenset(models)
                for role, models in sorted(failed_models.items())
                if models
            },
            unmatched_attempts=tuple(pending),
            invalid_replay_authorization=invalid_replay_authorization,
        )

    @staticmethod
    def _model_attempt_identity(event: SwarmEvent) -> tuple[str, str] | None:
        role = event.payload.get("role")
        if _is_non_workflow_role(role):
            return None
        model = event.payload.get("model")
        if (
            not isinstance(role, str)
            or role not in _ROLE_PREREQUISITES
            or role == "verifier"
            or not isinstance(model, str)
            or not model.strip()
        ):
            raise RuntimeError("Swarm run has an invalid durable model attempt")
        return role, model.strip()

    @staticmethod
    def _workflow_completion_identity(
        event: SwarmEvent,
    ) -> tuple[str, str] | None:
        role = event.payload.get("role")
        if _is_non_workflow_role(role):
            return None
        if not isinstance(role, str) or role not in _ROLE_PREREQUISITES:
            raise RuntimeError("Swarm run has an unknown durable workflow role")
        model = event.payload.get("model")
        if role == "verifier" and model is None:
            return None
        if not isinstance(model, str) or not model.strip():
            raise RuntimeError(
                f"Swarm run has an invalid legacy model record for role: {role}"
            )
        return role, model.strip()

    @staticmethod
    def _replay_authorization_identity(
        event: SwarmEvent,
    ) -> tuple[int, str, str] | None:
        payload = event.payload
        if set(payload) != {
            "actor_id",
            "original_attempt_sequence",
            "role",
            "model",
        }:
            return None
        actor_id = payload.get("actor_id")
        original_sequence = payload.get("original_attempt_sequence")
        role = payload.get("role")
        model = payload.get("model")
        if (
            not isinstance(actor_id, str)
            or not actor_id.startswith(("os:", "dashboard:"))
            or not actor_id.split(":", 1)[1].strip()
            or len(actor_id) > 256
            or any(
                ord(character) < 32 or ord(character) == 127 for character in actor_id
            )
            or isinstance(original_sequence, bool)
            or not isinstance(original_sequence, int)
            or original_sequence < 1
            or not isinstance(role, str)
            or role not in _ROLE_PREREQUISITES
            or role == "verifier"
            or not isinstance(model, str)
            or not model.strip()
            or model != model.strip()
        ):
            return None
        return original_sequence, role, model

    @staticmethod
    def _consume_pending_attempt(
        pending: list[_UnmatchedModelAttempt],
        role: str,
        model: str,
    ) -> None:
        for index, attempt in enumerate(pending):
            if attempt.role == role and attempt.model == model:
                pending.pop(index)
                return

    def _transport_supports_idempotent_replay(self) -> bool:
        """Return the explicit transport guarantee required to replay a call."""
        return bool(getattr(self.transport, "supports_idempotent_replay", False))

    @staticmethod
    def _response_from_checkpoint(
        checkpoint: WorkflowRoleCheckpoint,
    ) -> ModelResponse:
        return ModelResponse(
            model=checkpoint.model or "",
            content="",
            data=dict(checkpoint.data),
        )

    @staticmethod
    def _prior_model_call_count(events: list[SwarmEvent]) -> int:
        """Count prior provider attempts; verifier work is intentionally local."""
        starts = [
            event for event in events if event.event_type == "model.attempt_started"
        ]
        first_started_sequence = starts[0].sequence if starts else None
        # Runs created before durable dispatch markers used one completed event
        # per successful provider call plus one failure event per unsuccessful
        # attempt.  Preserve that legacy prefix exactly once: after the first
        # marker, each provider call already has a durable start event and
        # counting its terminal event again would double-charge the budget.
        legacy_prefix_count = sum(
            1
            for event in events
            if (
                first_started_sequence is None
                or event.sequence < first_started_sequence
            )
            if event.event_type == "model.attempt_failed"
            or (
                event.event_type == "work.completed"
                and isinstance(event.payload.get("model"), str)
                and bool(event.payload["model"].strip())
            )
        )
        return legacy_prefix_count + len(starts)

    @staticmethod
    def _record_model_attempt_started(
        store: ProjectSwarmStore,
        run_id: str,
        attempt: ModelAttemptStarted,
    ) -> None:
        store.append_event(
            run_id,
            "model.attempt_started",
            {"role": attempt.role, "model": attempt.model},
        )

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
