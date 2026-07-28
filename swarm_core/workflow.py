"""Role execution, call limits, context sharding, and coding-team workflow."""

from __future__ import annotations

from concurrent.futures import as_completed, ThreadPoolExecutor
from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import threading
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping

from .models import ModelRequest, ModelResponse
from .router import ModelRouter, NoEligibleModel
from .transport import ModelTransport, RetryableModelTransportError
from .types import thaw_json_value
from .verifier import (
    DefaultReadOnlyVerifier,
    InvalidVerifierResult,
    ReadOnlyVerifier,
    VerificationRequest,
    validate_independent_result,
    verification_result_from_checkpoint_data,
)


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
    def __init__(self, limit: int = 48, *, initial_used: int = 0) -> None:
        if limit < 1:
            raise ValueError("Call budget must be positive")
        if not isinstance(initial_used, int) or initial_used < 0:
            raise ValueError("Initial call budget use must be a non-negative integer")
        self.limit = limit
        self._used = initial_used
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
    checkpoint_bindings: Mapping[str, str] = field(
        default_factory=lambda: MappingProxyType({})
    )

    def __post_init__(self) -> None:
        bindings = {
            str(field_name): str(value)
            for field_name, value in self.checkpoint_bindings.items()
        }
        object.__setattr__(
            self,
            "checkpoint_bindings",
            MappingProxyType(bindings),
        )


@dataclass(frozen=True)
class ModelAttemptFailure:
    role: str
    model: str
    reason: str


@dataclass(frozen=True)
class ModelAttemptStarted:
    """One provider call durably claimed before its transport dispatch."""

    role: str
    model: str


class ModelExecutor:
    """Apply role routing, retries, schema checks, and global safety limits."""

    def __init__(
        self,
        router: ModelRouter,
        transport: ModelTransport,
        *,
        call_budget: CallBudget | None = None,
        max_concurrent: int = 3,
        before_model_call: Callable[[], None] | None = None,
        on_model_attempt_started: Callable[[ModelAttemptStarted], None] | None = None,
        prior_failed_models: Mapping[str, Iterable[str]] | None = None,
    ) -> None:
        if not 1 <= max_concurrent <= 3:
            raise ValueError("Swarm concurrency must be between 1 and 3")
        self.router = router
        self.transport = transport
        self.call_budget = call_budget or CallBudget()
        self.max_concurrent = max_concurrent
        self.before_model_call = before_model_call
        self.on_model_attempt_started = on_model_attempt_started
        self.prior_failed_models = {
            str(role): frozenset(
                str(model).strip() for model in models if str(model).strip()
            )
            for role, models in (prior_failed_models or {}).items()
            if str(role).strip()
        }

    def complete(
        self,
        call: RoleCall,
        *,
        run_id: str,
        on_failure: Callable[[ModelAttemptFailure], None] | None = None,
    ) -> ModelResponse:
        selection = self._selection_for(call)
        attempted: list[str] = []
        for model in selection.models:
            if model in self.prior_failed_models.get(call.role, frozenset()):
                # This exact role/model attempt is already durably known to
                # have failed before a host restart.  It remains part of the
                # explanatory chain, but must not consume another provider
                # slot or call-budget unit by being replayed.
                attempted.append(model)
                continue
            if self.before_model_call is not None:
                self.before_model_call()
            self.call_budget.claim(role=call.role, attempted_models=attempted)
            attempted.append(model)
            if self.on_model_attempt_started is not None:
                # This durable marker is intentionally synchronous and comes
                # before the external provider call.  A process crash after
                # dispatch cannot reset the run-wide call budget on resume.
                self.on_model_attempt_started(ModelAttemptStarted(call.role, model))
            request = ModelRequest(
                run_id=run_id,
                role=call.role,
                model=model,
                prompt=call.prompt,
                # Every provider attempt receives its own plain JSON copy.
                # Event emitters and a parallel sibling therefore cannot
                # mutate the immutable host-owned authorization source.
                context=thaw_json_value(call.context),
                required_fields=call.required_fields,
                provider=selection.provider,
            )
            try:
                with _TRANSPORT_CALL_SLOTS:
                    response = self.transport.complete(request)
            except RetryableModelTransportError:
                if on_failure is not None:
                    on_failure(ModelAttemptFailure(call.role, model, "call_error"))
                continue
            response_failure = _response_failure_reason(response, call.required_fields)
            if response_failure is None:
                binding_failure = _checkpoint_binding_failure(
                    response,
                    call.checkpoint_bindings,
                )
                if binding_failure is None:
                    return _bind_checkpoint_response(
                        response,
                        call.checkpoint_bindings,
                    )
                response_failure = binding_failure
            if on_failure is not None:
                on_failure(ModelAttemptFailure(call.role, model, response_failure))
        raise WorkflowPaused(
            "model_chain_exhausted",
            role=call.role,
            attempted_models=attempted,
        )

    def _selection_for(self, call: RoleCall):
        try:
            return self.router.select(call.role, call.requirements)
        except NoEligibleModel as exc:
            raise WorkflowPaused(
                "no_eligible_model",
                role=call.role,
                attempted_models=(),
            ) from exc

    def _ensure_call_has_unfailed_model(self, call: RoleCall) -> None:
        selection = self._selection_for(call)
        if all(
            model in self.prior_failed_models.get(call.role, frozenset())
            for model in selection.models
        ):
            raise WorkflowPaused(
                "model_chain_exhausted",
                role=call.role,
                attempted_models=selection.models,
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
        # Preflight the complete batch in role order.  If Builder's whole
        # route is already durably exhausted, dispatching a still-pending
        # Critic would spend a new provider call on a stage that cannot reach
        # Verifier anyway.
        for call in calls:
            self._ensure_call_has_unfailed_model(call)
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
                if on_success is not None:
                    # Persist each successful sibling at the moment it
                    # completes.  Waiting for the whole parallel batch here
                    # creates a crash window in which Builder may have a
                    # valid result while a slow Critic prevents its durable
                    # checkpoint from being written.
                    try:
                        on_success(calls[index], response)
                    except BaseException as exc:
                        # Preserve the deterministic error selection below:
                        # callbacks from a later-completing sibling must not
                        # make its failure win over an earlier role.
                        errors[index] = exc
        for index, call_failures in enumerate(failures):
            if on_failure is not None:
                for failure in call_failures:
                    on_failure(failure)
        for error in errors:
            if error is not None:
                raise error
        return [response for response in responses if response is not None]


def _response_failure_reason(
    response: ModelResponse,
    required_fields: Iterable[str],
) -> str | None:
    """Classify only response states that may safely use a model fallback."""
    if not isinstance(response.content, str) or not response.content.strip():
        return "empty_response"
    data = response.data
    if not isinstance(data, Mapping):
        return "schema_invalid"
    if any(field not in data for field in required_fields):
        return "schema_invalid"
    if not isinstance(data.get("work"), str):
        return "schema_invalid"
    if not isinstance(data.get("evidence"), list):
        return "schema_invalid"
    if not isinstance(data.get("decision"), str):
        return "schema_invalid"
    if "approved" in required_fields and not isinstance(data.get("approved"), bool):
        return "schema_invalid"
    return None


def _checkpoint_binding_failure(
    response: ModelResponse,
    bindings: Mapping[str, str],
) -> str | None:
    """Reject a model response that conflicts with host-owned checkpoint keys."""
    for field_name, expected in bindings.items():
        if field_name in response.data and response.data.get(field_name) != expected:
            return "authorization_binding_mismatch"
    return None


def _bind_checkpoint_response(
    response: ModelResponse,
    bindings: Mapping[str, str],
) -> ModelResponse:
    if not bindings:
        return response
    data = dict(response.data)
    data.update(bindings)
    return ModelResponse(
        model=response.model,
        content=response.content,
        data=MappingProxyType(data),
    )


_REVIEW_AUTHORIZATION_FIELDS = frozenset(
    {
        "action",
        "target",
        "payload",
        "expected_output_scope",
        "intent_digest",
        "proposal_digest",
    }
)
_REVIEW_AUTHORIZATION_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_REVIEW_AUTHORIZATION_BYTES = 16 * 1024


def _review_authorization_context(
    verifier: ReadOnlyVerifier,
) -> Mapping[str, Any] | None:
    """Read one optional, bounded host-owned action context from a verifier."""
    resolver = getattr(verifier, "review_authorization_context", None)
    if resolver is None:
        return None
    if not callable(resolver):
        raise InvalidVerifierResult("review authorization context is not callable")
    raw = resolver()
    if not isinstance(raw, Mapping) or set(raw) != _REVIEW_AUTHORIZATION_FIELDS:
        raise InvalidVerifierResult("review authorization context has invalid fields")
    if (
        not isinstance(raw.get("action"), str)
        or not raw["action"].strip()
        or not isinstance(raw.get("expected_output_scope"), str)
        or not raw["expected_output_scope"].strip()
        or not isinstance(raw.get("target"), Mapping)
        or not isinstance(raw.get("payload"), Mapping)
        or not _REVIEW_AUTHORIZATION_DIGEST.fullmatch(
            str(raw.get("intent_digest") or "")
        )
        or not _REVIEW_AUTHORIZATION_DIGEST.fullmatch(
            str(raw.get("proposal_digest") or "")
        )
    ):
        raise InvalidVerifierResult("review authorization context is invalid")
    try:
        encoded = json.dumps(
            raw,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        canonical = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise InvalidVerifierResult(
            "review authorization context is not JSON-safe"
        ) from exc
    if len(encoded) > _MAX_REVIEW_AUTHORIZATION_BYTES:
        raise InvalidVerifierResult("review authorization context is too large")
    frozen = _freeze_json_value(canonical)
    if not isinstance(frozen, Mapping):  # pragma: no cover - canonical is a dict
        raise InvalidVerifierResult("review authorization context is invalid")
    return frozen


def _freeze_json_value(value: Any) -> Any:
    """Deep-freeze one already validated JSON value without changing meaning."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(field_name): _freeze_json_value(child)
                for field_name, child in value.items()
            }
        )
    if isinstance(value, list):
        return tuple(_freeze_json_value(child) for child in value)
    return value


class Blackboard:
    def __init__(self, initial: Mapping[str, Any]) -> None:
        self._values = dict(initial)

    def put(self, key: str, value: Any) -> None:
        self._values[key] = value

    def shard(self, *keys: str) -> dict[str, Any]:
        return {key: self._values[key] for key in keys}


@dataclass(frozen=True)
class WorkflowProfile:
    """Static, non-executable role aliases for one shipped pack workflow."""

    workflow: str
    role_aliases: Mapping[str, str]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "role_aliases",
            MappingProxyType(dict(self.role_aliases)),
        )

    def alias_for(self, stage: str) -> str:
        try:
            return self.role_aliases[stage]
        except KeyError as exc:  # pragma: no cover - protects shipped profiles
            raise ValueError(
                f"Workflow profile {self.workflow!r} has no alias for {stage!r}"
            ) from exc


_CORE_STAGES = (
    "scout",
    "planner",
    "planner_challenger",
    "planner_arbitrator",
    "builder",
    "critic",
    "verifier",
    "review_a",
    "review_b",
    "integrator",
    "referee",
)

_WORKFLOW_PROFILES: Mapping[str, WorkflowProfile] = MappingProxyType(
    {
        "coding-team": WorkflowProfile(
            "coding-team", {stage: stage for stage in _CORE_STAGES}
        ),
        "bug-hunt": WorkflowProfile(
            "bug-hunt",
            {
                "scout": "scout",
                "planner": "investigator",
                "planner_challenger": "investigator",
                "planner_arbitrator": "investigator",
                "builder": "investigator",
                "critic": "verifier",
                "verifier": "verifier",
                "review_a": "verifier",
                "review_b": "verifier",
                "integrator": "verifier",
                "referee": "verifier",
            },
        ),
        "research-team": WorkflowProfile(
            "research-team",
            {
                "scout": "scout",
                "planner": "analyst",
                "planner_challenger": "analyst",
                "planner_arbitrator": "analyst",
                "builder": "analyst",
                "critic": "reviewer",
                "verifier": "reviewer",
                "review_a": "reviewer",
                "review_b": "reviewer",
                "integrator": "analyst",
                "referee": "reviewer",
            },
        ),
        "release-audit": WorkflowProfile(
            "release-audit",
            {
                "scout": "scout",
                "planner": "auditor",
                "planner_challenger": "auditor",
                "planner_arbitrator": "auditor",
                "builder": "auditor",
                "critic": "reviewer",
                "verifier": "reviewer",
                "review_a": "reviewer",
                "review_b": "reviewer",
                "integrator": "auditor",
                "referee": "reviewer",
            },
        ),
    }
)


def workflow_profile_for(workflow: str) -> WorkflowProfile:
    """Return the fixed safe alias profile for a declared pack workflow."""
    normalized = str(workflow).strip()
    try:
        return _WORKFLOW_PROFILES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported Swarm pack workflow: {workflow!r}") from exc


_PLANNER_CONFLICT_DECISIONS = frozenset(
    {"conflict", "conflict_detected", "needs_challenger"}
)


def _has_explicit_planner_conflict(*responses: ModelResponse) -> bool:
    """Require an explicit structured conflict signal before extra model work.

    Natural-language disagreement must not spend an extra Cloud call by
    accident.  A producer must set ``conflict: true`` (directly or on a
    structured evidence item) or use one of the exact decision enum values.
    The complete response is checkpointed before this detector runs, making
    the trigger stable across a resumed run.
    """
    for response in responses:
        data = response.data
        if not isinstance(data, Mapping):
            continue
        if data.get("conflict") is True:
            return True
        decision = data.get("decision")
        if (
            isinstance(decision, str)
            and decision.strip().lower() in _PLANNER_CONFLICT_DECISIONS
        ):
            return True
        evidence = data.get("evidence")
        if isinstance(evidence, list) and any(
            isinstance(item, Mapping) and item.get("conflict") is True
            for item in evidence
        ):
            return True
    return False


@dataclass(frozen=True)
class WorkflowOutcome:
    evidence: Mapping[str, list[Any]]
    decision: str


EventEmitter = Callable[[str, Mapping[str, Any]], None]


class CodingTeamWorkflow:
    """Scout through referee with a local evidence-oriented verifier.

    Every shipped pack shares this reviewed execution spine.  A pack only
    narrows each role's prompt; it never bypasses the verifier, independent
    review pair, integrator or referee.
    """

    def __init__(
        self,
        *,
        pack_id: str = "coding-team",
        workflow: str = "coding-team",
        pack_description: str = "",
        pack_roles: Mapping[str, str] | None = None,
        verifier: ReadOnlyVerifier | None = None,
    ) -> None:
        self.pack_id = str(pack_id).strip() or "coding-team"
        self.profile = workflow_profile_for(workflow)
        self.pack_description = str(pack_description).strip()
        self.pack_roles = dict(pack_roles or {})
        self.verifier = verifier if verifier is not None else DefaultReadOnlyVerifier()

    def run(
        self,
        *,
        run_id: str,
        goal: str,
        project_root: Path,
        executor: ModelExecutor,
        emit: EventEmitter,
        completed_responses: Mapping[str, ModelResponse] | None = None,
        record_response: Callable[[str, ModelResponse], ModelResponse] | None = None,
    ) -> WorkflowOutcome:
        board = Blackboard({"goal": goal, "project_root": str(project_root.resolve())})
        evidence: dict[str, list[Any]] = {}
        completed = dict(completed_responses or {})

        scout = self._complete_or_restore_one(
            executor,
            run_id,
            RoleCall(
                "scout",
                self._prompt("scout", "Inspect the project and report relevant facts."),
                board.shard("goal", "project_root"),
            ),
            emit,
            completed,
            record_response,
        )
        self._record("scout", scout, "scout", board, evidence)

        planner = self._complete_or_restore_one(
            executor,
            run_id,
            RoleCall(
                "planner",
                self._prompt("planner", "Create a concrete implementation plan."),
                board.shard("goal", "scout"),
            ),
            emit,
            completed,
            record_response,
        )
        self._record("planner", planner, "plan", board, evidence)

        # A challenger is deliberately not part of Planner's normal fallback
        # chain.  It is additional independent work only when the durable
        # Scout/Planner outputs carry an explicit structured conflict signal.
        if _has_explicit_planner_conflict(scout, planner):
            challenger = self._complete_or_restore_one(
                executor,
                run_id,
                RoleCall(
                    "planner_challenger",
                    self._prompt(
                        "planner_challenger",
                        "Independently challenge the plan for the declared conflict.",
                    ),
                    board.shard("goal", "scout", "plan"),
                    frozenset({"planning", "structured-output"}),
                ),
                emit,
                completed,
                record_response,
            )
            self._record(
                "planner_challenger",
                challenger,
                "planner_challenge",
                board,
                evidence,
            )
            planner = self._complete_or_restore_one(
                executor,
                run_id,
                RoleCall(
                    "planner_arbitrator",
                    self._prompt(
                        "planner_arbitrator",
                        "Arbitrate the primary plan and independent challenge into one plan.",
                    ),
                    board.shard("goal", "scout", "plan", "planner_challenge"),
                    frozenset({"planning", "structured-output"}),
                ),
                emit,
                completed,
                record_response,
            )
            self._record("planner_arbitrator", planner, "plan", board, evidence)

        build_calls = (
            RoleCall(
                "builder",
                self._prompt("builder", "Build the planned result."),
                board.shard("goal", "plan"),
            ),
            RoleCall(
                "critic",
                self._prompt("critic", "Critique risks and omissions in the plan."),
                board.shard("goal", "plan"),
            ),
        )
        builder, critic = self._complete_parallel(
            executor,
            run_id,
            build_calls,
            emit,
            completed,
            record_response,
        )
        self._record("builder", builder, "build", board, evidence)
        self._record("critic", critic, "critique", board, evidence)

        verification_response = completed.get("verifier")
        verification_request = VerificationRequest(
            run_id=run_id,
            goal=goal,
            project_root=project_root,
            builder=builder.data,
            critic=critic.data,
        )
        if verification_response is None:
            verification_context = board.shard("goal", "build", "critique")
            emit(
                "work.started",
                {"role": "verifier", "context": verification_context},
            )
            try:
                verification_result = validate_independent_result(
                    self.verifier.verify(verification_request),
                    verification_request,
                )
            except (InvalidVerifierResult, TypeError, ValueError) as exc:
                raise WorkflowPaused(
                    "invalid_verifier_result",
                    role="verifier",
                ) from exc
            verification_response = ModelResponse(
                model="",
                content="local verifier completed",
                data=verification_result.to_checkpoint_data(),
            )
            verification_response = self._persist_response(
                "verifier",
                verification_response,
                emit,
                record_response,
            )
            completed["verifier"] = verification_response
        else:
            try:
                validate_independent_result(
                    verification_result_from_checkpoint_data(
                        verification_response.data
                    ),
                    verification_request,
                )
            except InvalidVerifierResult as exc:
                raise WorkflowPaused(
                    "invalid_verifier_result",
                    role="verifier",
                ) from exc
        verification = dict(verification_response.data)
        board.put("verification", verification)
        evidence["verifier"] = list(verification["evidence"])

        review_context = board.shard("goal", "build", "critique", "verification")
        try:
            authorization_context = _review_authorization_context(self.verifier)
        except (InvalidVerifierResult, TypeError, ValueError) as exc:
            raise WorkflowPaused(
                "invalid_verifier_result",
                role="verifier",
            ) from exc
        review_bindings: Mapping[str, str] = MappingProxyType({})
        if authorization_context is not None:
            review_context["authorization_context"] = authorization_context
            review_bindings = MappingProxyType(
                {
                    "intent_digest": authorization_context["intent_digest"],
                    "proposal_digest": authorization_context["proposal_digest"],
                }
            )
        review_a, review_b = self._complete_parallel(
            executor,
            run_id,
            (
                RoleCall(
                    "review_a",
                    self._prompt(
                        "review_a",
                        (
                            "Perform independent review A. Return an explicit JSON "
                            "boolean `approved`; use decision `approve` or `approved` "
                            "only when it is true, otherwise record a blocking decision. "
                            "When `authorization_context` is present, review that exact "
                            "action, target, payload, output scope, and digest binding."
                        ),
                    ),
                    review_context,
                    frozenset({"review", "structured-output"}),
                    ("work", "evidence", "decision", "approved"),
                    review_bindings,
                ),
                RoleCall(
                    "review_b",
                    self._prompt(
                        "review_b",
                        (
                            "Perform independent review B. Return an explicit JSON "
                            "boolean `approved`; use decision `approve` or `approved` "
                            "only when it is true, otherwise record a blocking decision. "
                            "When `authorization_context` is present, review that exact "
                            "action, target, payload, output scope, and digest binding."
                        ),
                    ),
                    review_context,
                    frozenset({"review", "structured-output"}),
                    ("work", "evidence", "decision", "approved"),
                    review_bindings,
                ),
            ),
            emit,
            completed,
            record_response,
        )
        self._record("review_a", review_a, "review_a", board, evidence)
        self._record("review_b", review_b, "review_b", board, evidence)
        board.put(
            "reviews",
            {"review_a": dict(review_a.data), "review_b": dict(review_b.data)},
        )

        integrator = self._complete_or_restore_one(
            executor,
            run_id,
            RoleCall(
                "integrator",
                self._prompt(
                    "integrator",
                    "Integrate the plan, work, verification, and reviews.",
                ),
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
            completed,
            record_response,
        )
        self._record("integrator", integrator, "integration", board, evidence)

        referee = self._complete_or_restore_one(
            executor,
            run_id,
            RoleCall(
                "referee",
                self._prompt("referee", "Issue the final evidence-backed decision."),
                board.shard("goal", "integration", "verification", "reviews"),
                frozenset({"referee", "structured-output"}),
            ),
            emit,
            completed,
            record_response,
        )
        self._record("referee", referee, "referee", board, evidence)
        return WorkflowOutcome(evidence=evidence, decision=referee.data["decision"])

    def _prompt(self, role: str, base: str) -> str:
        if self.pack_id == "coding-team":
            return base
        alias = self.profile.alias_for(role)
        role_focus = self.pack_roles.get(alias) or self.pack_roles.get(role)
        detail = f"\nPack: {self.pack_id}"
        detail += f"\nWorkflow profile: {self.profile.workflow}"
        detail += f"\nRole alias: {alias}"
        if self.pack_description:
            detail += f"\nPack purpose: {self.pack_description}"
        if role_focus:
            detail += f"\nPack role focus: {role_focus}"
        return base + detail

    @staticmethod
    def _complete_or_restore_one(
        executor: ModelExecutor,
        run_id: str,
        call: RoleCall,
        emit: EventEmitter,
        completed: dict[str, ModelResponse],
        record_response: Callable[[str, ModelResponse], ModelResponse] | None,
    ) -> ModelResponse:
        restored = completed.get(call.role)
        if restored is not None:
            return restored
        emit(
            "work.started",
            {"role": call.role, "context": thaw_json_value(call.context)},
        )
        response = executor.complete(
            call,
            run_id=run_id,
            on_failure=lambda failure: CodingTeamWorkflow._emit_failure(failure, emit),
        )
        persisted = CodingTeamWorkflow._persist_response(
            call.role,
            response,
            emit,
            record_response,
        )
        completed[call.role] = persisted
        return persisted

    @staticmethod
    def _complete_parallel(
        executor: ModelExecutor,
        run_id: str,
        calls: tuple[RoleCall, ...],
        emit: EventEmitter,
        completed: dict[str, ModelResponse],
        record_response: Callable[[str, ModelResponse], ModelResponse] | None,
    ) -> list[ModelResponse]:
        responses: dict[str, ModelResponse] = {}
        pending: list[RoleCall] = []
        for call in calls:
            restored = completed.get(call.role)
            if restored is not None:
                responses[call.role] = restored
                continue
            emit(
                "work.started",
                {"role": call.role, "context": thaw_json_value(call.context)},
            )
            pending.append(call)
        if pending:

            def record_success(call: RoleCall, response: ModelResponse) -> None:
                persisted = CodingTeamWorkflow._persist_response(
                    call.role,
                    response,
                    emit,
                    record_response,
                )
                completed[call.role] = persisted
                responses[call.role] = persisted

            executor.complete_many(
                pending,
                run_id=run_id,
                on_success=record_success,
                on_failure=lambda failure: CodingTeamWorkflow._emit_failure(
                    failure, emit
                ),
            )
        return [responses[call.role] for call in calls]

    @staticmethod
    def _persist_response(
        role: str,
        response: ModelResponse,
        emit: EventEmitter,
        record_response: Callable[[str, ModelResponse], ModelResponse] | None,
    ) -> ModelResponse:
        if record_response is not None:
            return record_response(role, response)
        CodingTeamWorkflow._emit_response(role, response, emit)
        return response

    @staticmethod
    def _emit_response(role: str, response: ModelResponse, emit: EventEmitter) -> None:
        work_payload: dict[str, Any] = {
            "role": role,
            "work": response.data["work"],
        }
        if response.model:
            work_payload["model"] = response.model
        emit(
            "work.completed",
            work_payload,
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
