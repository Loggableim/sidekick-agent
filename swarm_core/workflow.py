"""Role execution, call limits, context sharding, and coding-team workflow."""

from __future__ import annotations

from concurrent.futures import as_completed, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Callable, Iterable, Mapping

from .models import ModelRequest, ModelResponse
from .router import ModelRouter, NoEligibleModel
from .transport import ModelTransport


_TRANSPORT_CALL_SLOTS = threading.BoundedSemaphore(3)


class WorkflowPaused(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        role: str,
        attempted_models: Iterable[str] = (),
    ) -> None:
        self.reason = reason
        self.role = role
        self.attempted_models = tuple(attempted_models)
        super().__init__(f"{reason}: {role}")


class CallBudget:
    def __init__(self, limit: int = 48) -> None:
        if limit < 1:
            raise ValueError("Call budget must be positive")
        self.limit = limit
        self._used = 0
        self._lock = threading.Lock()

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    def claim(self, *, role: str, attempted_models: Iterable[str]) -> None:
        with self._lock:
            if self._used >= self.limit:
                raise WorkflowPaused(
                    "call_budget_exhausted",
                    role=role,
                    attempted_models=attempted_models,
                )
            self._used += 1


@dataclass(frozen=True)
class RoleCall:
    role: str
    prompt: str
    context: Mapping[str, Any]
    requirements: frozenset[str] = frozenset({"structured-output"})
    required_fields: tuple[str, ...] = ("work", "evidence", "decision")


@dataclass(frozen=True)
class ModelAttemptFailure:
    role: str
    model: str
    reason: str


class ModelExecutor:
    """Apply role routing, retries, schema checks, and global safety limits."""

    def __init__(
        self,
        router: ModelRouter,
        transport: ModelTransport,
        *,
        call_budget: CallBudget | None = None,
        max_concurrent: int = 3,
    ) -> None:
        if not 1 <= max_concurrent <= 3:
            raise ValueError("Swarm concurrency must be between 1 and 3")
        self.router = router
        self.transport = transport
        self.call_budget = call_budget or CallBudget()
        self.max_concurrent = max_concurrent

    def complete(
        self,
        call: RoleCall,
        *,
        run_id: str,
        on_failure: Callable[[ModelAttemptFailure], None] | None = None,
    ) -> ModelResponse:
        try:
            selection = self.router.select(call.role, call.requirements)
        except NoEligibleModel as exc:
            raise WorkflowPaused(
                "no_eligible_model",
                role=call.role,
                attempted_models=(),
            ) from exc
        attempted: list[str] = []
        for model in selection.models:
            self.call_budget.claim(role=call.role, attempted_models=attempted)
            attempted.append(model)
            request = ModelRequest(
                run_id=run_id,
                role=call.role,
                model=model,
                prompt=call.prompt,
                context=call.context,
                required_fields=call.required_fields,
                provider=selection.provider,
            )
            try:
                with _TRANSPORT_CALL_SLOTS:
                    response = self.transport.complete(request)
            except Exception:
                if on_failure is not None:
                    on_failure(ModelAttemptFailure(call.role, model, "call_error"))
                continue
            if _valid_response(response, call.required_fields):
                return response
            if on_failure is not None:
                on_failure(ModelAttemptFailure(call.role, model, "schema_invalid"))
        raise WorkflowPaused(
            "model_chain_exhausted",
            role=call.role,
            attempted_models=attempted,
        )

    def complete_many(
        self,
        calls: Iterable[RoleCall],
        *,
        run_id: str,
        on_success: Callable[[RoleCall, ModelResponse], None] | None = None,
        on_failure: Callable[[ModelAttemptFailure], None] | None = None,
    ) -> list[ModelResponse]:
        calls = tuple(calls)
        responses: list[ModelResponse | None] = [None] * len(calls)
        failures: list[list[ModelAttemptFailure]] = [[] for _call in calls]
        errors: list[BaseException | None] = [None] * len(calls)
        with ThreadPoolExecutor(max_workers=self.max_concurrent) as pool:
            futures = {
                pool.submit(
                    self.complete,
                    call,
                    run_id=run_id,
                    on_failure=failures[index].append,
                ): index
                for index, call in enumerate(calls)
            }
            for future in as_completed(futures):
                index = futures[future]
                try:
                    response = future.result()
                except BaseException as exc:
                    errors[index] = exc
                    continue
                responses[index] = response
        for index, call_failures in enumerate(failures):
            if on_failure is not None:
                for failure in call_failures:
                    on_failure(failure)
            response = responses[index]
            if response is not None and on_success is not None:
                on_success(calls[index], response)
        for error in errors:
            if error is not None:
                raise error
        return [response for response in responses if response is not None]


def _valid_response(response: ModelResponse, required_fields: Iterable[str]) -> bool:
    data = response.data
    if not isinstance(data, Mapping):
        return False
    if any(field not in data for field in required_fields):
        return False
    if not isinstance(data.get("work"), str):
        return False
    if not isinstance(data.get("evidence"), list):
        return False
    if not isinstance(data.get("decision"), str):
        return False
    return True


class Blackboard:
    def __init__(self, initial: Mapping[str, Any]) -> None:
        self._values = dict(initial)

    def put(self, key: str, value: Any) -> None:
        self._values[key] = value

    def shard(self, *keys: str) -> dict[str, Any]:
        return {key: self._values[key] for key in keys}


@dataclass(frozen=True)
class WorkflowOutcome:
    evidence: Mapping[str, list[Any]]
    decision: str


EventEmitter = Callable[[str, Mapping[str, Any]], None]


class CodingTeamWorkflow:
    """Scout through referee with a local evidence-oriented verifier."""

    def run(
        self,
        *,
        run_id: str,
        goal: str,
        project_root: Path,
        executor: ModelExecutor,
        emit: EventEmitter,
    ) -> WorkflowOutcome:
        board = Blackboard({"goal": goal, "project_root": str(project_root.resolve())})
        evidence: dict[str, list[Any]] = {}

        scout = self._complete_one(
            executor,
            run_id,
            RoleCall(
                "scout",
                "Inspect the project and report relevant facts.",
                board.shard("goal", "project_root"),
            ),
            emit,
        )
        self._record("scout", scout, "scout", board, evidence)

        planner = self._complete_one(
            executor,
            run_id,
            RoleCall(
                "planner",
                "Create a concrete implementation plan.",
                board.shard("goal", "scout"),
            ),
            emit,
        )
        self._record("planner", planner, "plan", board, evidence)

        build_calls = (
            RoleCall(
                "builder",
                "Build the planned result.",
                board.shard("goal", "plan"),
            ),
            RoleCall(
                "critic",
                "Critique risks and omissions in the plan.",
                board.shard("goal", "plan"),
            ),
        )
        builder, critic = self._complete_parallel(executor, run_id, build_calls, emit)
        self._record("builder", builder, "build", board, evidence)
        self._record("critic", critic, "critique", board, evidence)

        verification_context = board.shard("goal", "build", "critique")
        emit(
            "work.started",
            {"role": "verifier", "context": verification_context},
        )
        verification = {
            "work": "Synthesized builder and critic evidence for review.",
            "evidence": [
                *list(builder.data["evidence"]),
                *list(critic.data["evidence"]),
            ],
            "decision": "ready_for_independent_review",
        }
        board.put("verification", verification)
        evidence["verifier"] = list(verification["evidence"])
        emit("work.completed", {"role": "verifier", "work": verification["work"]})
        emit(
            "evidence.recorded",
            {"role": "verifier", "evidence": verification["evidence"]},
        )
        emit(
            "decision.recorded",
            {"role": "verifier", "decision": verification["decision"]},
        )

        review_context = board.shard("goal", "build", "critique", "verification")
        review_a, review_b = self._complete_parallel(
            executor,
            run_id,
            (
                RoleCall(
                    "review_a",
                    "Perform independent review A.",
                    review_context,
                    frozenset({"review", "structured-output"}),
                ),
                RoleCall(
                    "review_b",
                    "Perform independent review B.",
                    review_context,
                    frozenset({"review", "structured-output"}),
                ),
            ),
            emit,
        )
        self._record("review_a", review_a, "review_a", board, evidence)
        self._record("review_b", review_b, "review_b", board, evidence)
        board.put(
            "reviews",
            {"review_a": dict(review_a.data), "review_b": dict(review_b.data)},
        )

        integrator = self._complete_one(
            executor,
            run_id,
            RoleCall(
                "integrator",
                "Integrate the plan, work, verification, and reviews.",
                board.shard(
                    "goal",
                    "plan",
                    "build",
                    "critique",
                    "verification",
                    "reviews",
                ),
                frozenset({"integration", "structured-output"}),
            ),
            emit,
        )
        self._record("integrator", integrator, "integration", board, evidence)

        referee = self._complete_one(
            executor,
            run_id,
            RoleCall(
                "referee",
                "Issue the final evidence-backed decision.",
                board.shard("goal", "integration", "verification", "reviews"),
                frozenset({"referee", "structured-output"}),
            ),
            emit,
        )
        self._record("referee", referee, "referee", board, evidence)
        return WorkflowOutcome(evidence=evidence, decision=referee.data["decision"])

    @staticmethod
    def _complete_one(
        executor: ModelExecutor,
        run_id: str,
        call: RoleCall,
        emit: EventEmitter,
    ) -> ModelResponse:
        emit(
            "work.started",
            {"role": call.role, "context": dict(call.context)},
        )
        response = executor.complete(
            call,
            run_id=run_id,
            on_failure=lambda failure: CodingTeamWorkflow._emit_failure(failure, emit),
        )
        CodingTeamWorkflow._emit_response(call.role, response, emit)
        return response

    @staticmethod
    def _complete_parallel(
        executor: ModelExecutor,
        run_id: str,
        calls: tuple[RoleCall, ...],
        emit: EventEmitter,
    ) -> list[ModelResponse]:
        for call in calls:
            emit(
                "work.started",
                {"role": call.role, "context": dict(call.context)},
            )
        return executor.complete_many(
            calls,
            run_id=run_id,
            on_success=lambda call, response: CodingTeamWorkflow._emit_response(
                call.role, response, emit
            ),
            on_failure=lambda failure: CodingTeamWorkflow._emit_failure(failure, emit),
        )

    @staticmethod
    def _emit_response(role: str, response: ModelResponse, emit: EventEmitter) -> None:
        emit(
            "work.completed",
            {"role": role, "model": response.model, "work": response.data["work"]},
        )
        emit(
            "evidence.recorded",
            {"role": role, "evidence": list(response.data["evidence"])},
        )
        emit(
            "decision.recorded",
            {"role": role, "decision": response.data["decision"]},
        )

    @staticmethod
    def _emit_failure(failure: ModelAttemptFailure, emit: EventEmitter) -> None:
        emit(
            "model.attempt_failed",
            {
                "role": failure.role,
                "model": failure.model,
                "reason": failure.reason,
            },
        )

    @staticmethod
    def _record(
        role: str,
        response: ModelResponse,
        board_key: str,
        board: Blackboard,
        evidence: dict[str, list[Any]],
    ) -> None:
        board.put(board_key, dict(response.data))
        evidence[role] = list(response.data["evidence"])
