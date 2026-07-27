from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import threading
import time
from typing import Any

import pytest

from swarm_core.models import ModelRegistry, ModelRequest, ModelResponse
from swarm_core.router import ModelRouter
from swarm_core.transport import ModelTransport, OllamaCloudTransport
from swarm_core.workflow import (
    CallBudget,
    ModelExecutor,
    RoleCall,
    WorkflowPaused,
)


def _response(request: ModelRequest, **data: Any) -> ModelResponse:
    payload = {
        "work": f"{request.role} work",
        "evidence": [f"{request.role} evidence"],
        "decision": f"{request.role} decision",
        **data,
    }
    return ModelResponse(model=request.model, content=payload["work"], data=payload)


class RecordingTransport(ModelTransport):
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return _response(request)


def test_router_uses_exact_ollama_only_role_chains_without_gpt_oss():
    """Catches role drift or a generic/cross-provider fallback entering a route."""
    router = ModelRouter(ModelRegistry())

    expected = {
        "default": ("deepseek-v4-flash",),
        "scout": ("deepseek-v4-flash",),
        "planner": ("deepseek-v4-pro", "kimi-k2.6"),
        "builder": ("minimax-m3",),
        "critic": ("minimax-m3",),
        "coding": ("glm-5.2",),
        "review_a": ("glm-5.2",),
        "review_b": ("kimi-k2.7-code",),
        "integrator": ("nemotron-3-super",),
        "referee": ("nemotron-3-super",),
    }

    for role, expected_models in expected.items():
        selection = router.select(role, requirements=set())
        assert selection.models == expected_models
        assert selection.provider == "ollama-cloud"
        assert all("gpt-oss" not in model.lower() for model in selection.models)


def test_independent_review_pair_uses_distinct_required_model_families():
    """Catches the two required reviews collapsing onto one model family."""
    first, second = ModelRouter(ModelRegistry()).select_review_pair()

    assert (first.model, second.model) == ("glm-5.2", "kimi-k2.7-code")
    assert first.family != second.family
    assert first.provider == second.provider == "ollama-cloud"


def test_vision_route_uses_qwen_only_when_the_catalog_reports_it_available():
    """Catches routing to an unavailable Qwen vision model instead of Gemma."""
    without_qwen = ModelRouter(ModelRegistry(catalog={"gemma4:31b"}))
    with_qwen = ModelRouter(ModelRegistry(catalog={"qwen3.5", "gemma4:31b"}))

    assert without_qwen.select("vision", {"vision"}).models == ("gemma4:31b",)
    assert with_qwen.select("vision", {"vision"}).models == (
        "qwen3.5",
        "gemma4:31b",
    )


def test_registry_discovers_only_available_models_with_required_capabilities():
    """Catches capability selection ignoring either the catalog or requirements."""
    registry = ModelRegistry(catalog={"qwen3.5", "glm-5.2", "deepseek-v4-flash"})

    vision = registry.discover({"vision"})
    coding = registry.discover({"coding", "structured-output"})

    assert [model.name for model in vision] == ["qwen3.5"]
    assert [model.name for model in coding] == ["glm-5.2"]


def test_ollama_transport_invokes_only_the_injected_call_with_explicit_route():
    """Catches core transport importing a live client or omitting provider/model."""
    calls: list[dict[str, Any]] = []

    @dataclass
    class Message:
        content: str

    @dataclass
    class Choice:
        message: Message

    @dataclass
    class SidekickResponse:
        choices: list[Choice]

    def sidekick_call(**kwargs: Any) -> SidekickResponse:
        calls.append(kwargs)
        return SidekickResponse(
            [Choice(Message('{"work":"ok","evidence":[],"decision":"go"}'))]
        )

    transport = OllamaCloudTransport(sidekick_call)
    request = ModelRequest(
        run_id="run-1",
        role="planner",
        model="deepseek-v4-pro",
        prompt="plan",
        context={"goal": "ship"},
        required_fields=("work", "evidence", "decision"),
    )

    response = transport.complete(request)

    assert calls == [
        {
            "task": "swarm",
            "provider": "ollama-cloud",
            "model": "deepseek-v4-pro",
            "messages": [
                {
                    "role": "user",
                    "content": request.render_prompt(),
                }
            ],
        }
    ]
    assert response.model == "deepseek-v4-pro"
    assert response.data == {"work": "ok", "evidence": [], "decision": "go"}


@pytest.mark.parametrize("first_failure", ["schema", "error"])
def test_executor_falls_back_within_the_planner_chain_on_schema_or_call_error(
    first_failure: str,
):
    """Catches malformed/error responses stopping before the role fallback."""

    class FallbackTransport(ModelTransport):
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            if request.model == "deepseek-v4-pro":
                if first_failure == "error":
                    raise RuntimeError("cloud call failed")
                return ModelResponse(
                    model=request.model,
                    content="missing structured decision",
                    data={"work": "partial"},
                )
            return _response(request)

    transport = FallbackTransport()
    executor = ModelExecutor(ModelRouter(ModelRegistry()), transport)

    result = executor.complete(
        RoleCall(
            role="planner",
            prompt="make a plan",
            context={"goal": "ship"},
        ),
        run_id="fallback-run",
    )

    assert result.model == "kimi-k2.6"
    assert [request.model for request in transport.requests] == [
        "deepseek-v4-pro",
        "kimi-k2.6",
    ]
    assert executor.call_budget.used == 2


def test_exhausted_role_chain_pauses_without_cross_provider_or_local_fallback():
    """Catches an exhausted Ollama role chain silently escaping to another route."""

    class FailingTransport(ModelTransport):
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        def complete(self, request: ModelRequest) -> ModelResponse:
            self.requests.append(request)
            raise RuntimeError(f"{request.model} unavailable")

    transport = FailingTransport()
    executor = ModelExecutor(ModelRouter(ModelRegistry()), transport)

    with pytest.raises(WorkflowPaused) as raised:
        executor.complete(
            RoleCall(role="planner", prompt="plan", context={}),
            run_id="paused-run",
        )

    assert raised.value.reason == "model_chain_exhausted"
    assert raised.value.attempted_models == ("deepseek-v4-pro", "kimi-k2.6")
    assert [request.model for request in transport.requests] == [
        "deepseek-v4-pro",
        "kimi-k2.6",
    ]
    assert {request.provider for request in transport.requests} == {"ollama-cloud"}


def test_all_retries_count_toward_the_strict_48_call_ceiling():
    """Catches retries bypassing or off-by-one errors in the global call budget."""
    transport = RecordingTransport()
    executor = ModelExecutor(
        ModelRouter(ModelRegistry()),
        transport,
        call_budget=CallBudget(limit=48),
    )
    call = RoleCall(role="scout", prompt="inspect", context={})

    for index in range(48):
        executor.complete(call, run_id=f"budget-{index}")

    with pytest.raises(WorkflowPaused) as raised:
        executor.complete(call, run_id="budget-overflow")

    assert raised.value.reason == "call_budget_exhausted"
    assert executor.call_budget.used == 48
    assert len(transport.requests) == 48


def test_parallel_execution_never_exceeds_three_active_model_calls():
    """Catches worker fan-out bypassing the three-call concurrency ceiling."""

    class ConcurrencyTransport(ModelTransport):
        def __init__(self) -> None:
            self._lock = threading.Lock()
            self.active = 0
            self.max_active = 0
            self.requests: list[ModelRequest] = []

        def complete(self, request: ModelRequest) -> ModelResponse:
            with self._lock:
                self.requests.append(request)
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            time.sleep(0.03)
            with self._lock:
                self.active -= 1
            return _response(request)

    transport = ConcurrencyTransport()
    executor = ModelExecutor(
        ModelRouter(ModelRegistry()),
        transport,
        max_concurrent=3,
    )
    calls = [
        RoleCall(role="scout", prompt=f"shard {index}", context={"shard": index})
        for index in range(7)
    ]

    results = executor.complete_many(calls, run_id="parallel-run")

    assert len(results) == 7
    assert Counter(response.model for response in results) == {"deepseek-v4-flash": 7}
    assert transport.max_active == 3
